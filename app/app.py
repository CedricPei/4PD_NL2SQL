import json
import sys
from flask import Flask, request
from langchain.sql_database import SQLDatabase
from sqlalchemy.orm import sessionmaker
import argparse
import os
import ast
from core.service import run_generation_mac, run_genration_din
from core.llm import sqlcoder, GPT, DeepSeek, modelhub_deepseek_coder_33b_instruct, modelhub_qwen1_5_72b_chat
from multiprocessing import Value

counter = Value('i', -1)
app = Flask(__name__)

print("=============Starting service==============")
parser = argparse.ArgumentParser()
parser.add_argument("--model_name", type=str, choices=[
                    "gpt-3.5-turbo", "sqlcoder-7b-2", "deepseek-coder-33b-instruct", "modelhub-deepseek-coder-33b-instruct", "modelhub_qwen1_5_72b_chat"], default="sqlcoder-7b-2")
parser.add_argument("--method", type=str,
                    choices=["mac", "din"], default="mac")
args = parser.parse_args()
model_name = args.model_name
method = args.method
print(f"model_name: {model_name}")


for_submit = True
if for_submit:
    from nl2sql_hub.datasource import DataSource, get_url
    datasource_path = os.getenv("NL2SQL_DATASOURCE_PATH")
    with open(os.path.join(datasource_path, "datasource.json"), "r") as f:
        ds = json.load(f)
        print(f"load datasource from {datasource_path}, content: \n{ds}\n")
        db_name = ds.get("name")
        db_description = ds.get("description")
        db_tables = ds.get("tables")
        datasource = DataSource.parse_obj(ds)
    ds_url = get_url(datasource)
else:
    with open("datasource.json", "r", encoding='UTF-8') as f:
        ds = json.load(f)
        print(f"load datasource from datasource.json, content: \n{ds}\n")
        db_name = ds.get("name")
        db_description = ds.get("description")
        db_tables = ds.get("tables")
        datasource = ds
        ds_url = "sqlite:///./concert_singer.db"

print(f"datasource url: {ds_url}\n")
db_tool = SQLDatabase.from_uri(database_uri=ds_url)


# 获取数据库类型
db_type = db_tool.dialect
print(f"database type: {db_type}")  # 输出mysql


# 获取数据库中的所有表名
tables = db_tool.get_table_names()
print(f"tables: {tables}")  # 输出['t1', 't2', 't3']

# 获取table info
table_info = {}
for table in tables:
    cur_table_info = db_tool.get_table_info([table])
    table_info[table] = cur_table_info
    print(f"table_info: {cur_table_info}")  # 输出建表语句以及3条数据示例
print("length of table_info: ", len(table_info))

# 获取db column context (column and corresponding distinct values)
Session = sessionmaker(bind=db_tool._engine)
session = Session()

column_values_dict = {}
table_column_values_dict = {}

for table in db_tool._metadata.sorted_tables:
    print(table)
    for k, v in table._columns.items():
        if str(v.type).startswith("VARCHAR") or str(v.type).startswith("TEXT") or str(v.type).startswith("CHAR"):
            distinct_names = [str(name[0]) for name in session.query(
                v).distinct().all() if name[0]]
            table_column_name = str(v)
            column_name = str(v).split(".")[1]
            if column_name not in column_values_dict:
                column_values_dict[column_name] = distinct_names
            else:
                column_values_dict[column_name] += list(
                    set(distinct_names) - set(column_values_dict[column_name]))
            if table_column_name not in table_column_values_dict:
                table_column_values_dict[table_column_name] = distinct_names
            else:
                table_column_values_dict[table_column_name] += list(
                    set(distinct_names) - set(table_column_values_dict[table_column_name]))
remove_list = ['nan', 'None']
for key in column_values_dict:
    column_values_dict[key] = [
        x for x in column_values_dict[key] if x not in remove_list]
for key in table_column_values_dict:
    table_column_values_dict[key] = [
        x for x in table_column_values_dict[key] if x not in remove_list]
print("column_values_dict: ", column_values_dict)
# print("table_column_values_dict: ", table_column_values_dict)

# select 10 random rows for each table, and use the values of the selected rows as example values
table_column_values_dict = {}
for table in db_tool._metadata.sorted_tables:
    columns = []
    for k, v in table._columns.items():
        if str(v.type).startswith("VARCHAR") or str(v.type).startswith("TEXT") or str(v.type).startswith("CHAR") or str(v.type).startswith("DATE"):
            columns.append(str(v).split(".")[1])
    columns_str = ", ".join(columns)
    if db_type == "mysql":
        SQL_command = f"SELECT {columns_str} FROM {table.name} ORDER BY RAND() LIMIT 10;"
    else:
        SQL_command = f"SELECT {columns_str} FROM {table.name} ORDER BY RANDOM() LIMIT 10;"
    results = db_tool.run(SQL_command)
    results_list = ast.literal_eval(results)
    for i, column in enumerate(columns):
        key = f"{table.name}.{column}"
        if key not in table_column_values_dict:
            table_column_values_dict[key] = []
        for result in results_list:
            table_column_values_dict[key].append(result[i])
print("table_column_values_dict: ", table_column_values_dict)


model_dict = {
    "gpt-3.5-turbo": GPT,
    "sqlcoder-7b-2": sqlcoder,
    "deepseek-coder-33b-instruct": DeepSeek,
    "modelhub-deepseek-coder-33b-instruct": modelhub_deepseek_coder_33b_instruct,
    "modelhub_qwen1_5_72b_chat": modelhub_qwen1_5_72b_chat
}

try:
    llm = model_dict[model_name]()
except Exception as e:
    print("Error in starting llm: ", e)
    sys.exit("Error in starting llm: ", e)


@ app.route("/")
def hello_world():
    return "<p>Hello, World!</p>"


@ app.route("/predict", methods=["POST"])
def predict():
    with counter.get_lock():
        counter.value += 1
        out = counter.value
    count = out
    content_type = request.headers.get("Content-Type")
    if content_type != "application/json":
        return {
            "success": False,
            "message": "Content-Type must be application/json"
        }
    print("count:", count)  # record the number of requests
    print("request:", request)
    request_json = request.json
    print("request_json:", request_json)
    question = request_json.get("natural_language_query")
    print(f"Question: {question}")

    sql_query = ""
    try:
        if method == "din":
            sql_query = run_genration_din(
                question, db_name, db_description, tables, table_info, llm, db_tool)
        else:
            sql_query = run_generation_mac(
                question, db_name, db_description, db_type, tables, table_info, column_values_dict, table_column_values_dict, llm)

    except Exception as e:
        print("Error: ", e)
        with open("records.json", "a", encoding='UTF-8') as f:
            record = {
                "count": count,
                "db": db_name,
                "question": question,
                "sql_query": str(e)
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {
            "success": False,
            "message": [str(e)]
        }
    print(f"Returned SQL query: {sql_query}")
    # save the count, db, question, and sql_query to the records json file
    with open("records.json", "a", encoding='UTF-8') as f:
        record = {
            "count": count,
            "db": db_name,
            "question": question,
            "sql_query": sql_query
        }
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    if count % 10 == 9:
        # print the records every 10 requests
        with open("records.json", "r", encoding='UTF-8') as f:
            records = f.readlines()
            print("records: ", records)
    return {
        "success": True,
        "sql_queries": [
            sql_query
        ]
    }


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=18080)
