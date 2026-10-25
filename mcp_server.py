import asyncio
import itertools
import re
from contextlib import closing
from neo4j import READ_ACCESS
from psycopg2 import connect
from psycopg2.extras import RealDictCursor
import mcp.server.stdio
from mcp.server import Server
from mcp.types import Tool, TextContent

from pipeline.config import get_neo4j_driver, get_pg_config, setting

MAX_ROWS = 500
QUERY_TIMEOUT_SECONDS = 15

def _pg_connection():
    # Never reuse the privileged account used to rebuild the data.
    cfg = get_pg_config()
    user = setting("MCP_PG_USER")
    if not user or user == cfg["user"]:
        raise ValueError("Configure a separate SELECT-only MCP_PG_USER.")
    return connect(host=cfg["host"], port=cfg["port"], dbname=cfg["dbname"],
                   user=user, password=setting("MCP_PG_PASSWORD"), connect_timeout=5,
                   options="-c default_transaction_read_only=on")

def _neo4j_driver():
    user = setting("MCP_NEO4J_USER")
    if not user or user == setting("NEO4J_USER", "neo4j"):
        raise ValueError("Configure a separate read-only MCP_NEO4J_USER.")
    return get_neo4j_driver(auth=(user, setting("MCP_NEO4J_PASSWORD")))

def _single_query(query, prefix):
    if not isinstance(query, str) or not query.strip() or len(query) > 20000:
        raise ValueError("Query must be a nonempty string of at most 20000 characters.")
    query = query.strip().removesuffix(";").rstrip()
    if ";" in query or re.search(r"--|/\*|//", query) or not re.match(prefix, query, re.I):
        raise ValueError("Only one read-only query without comments is allowed.")
    return query

def _read_neo4j(query, trusted=False):
    if not trusted:
        query = _single_query(query, r"^(?:MATCH|OPTIONAL\s+MATCH|WITH|RETURN)\b")
        if re.search(r"\b(?:CALL|CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD|FOREACH)\b", query, re.I):
            raise ValueError("Write clauses and procedures are not allowed.")
    with _neo4j_driver() as driver:
        with driver.session(default_access_mode=READ_ACCESS) as session:
            with session.begin_transaction(timeout=QUERY_TIMEOUT_SECONDS) as tx:
                if tx.run("EXPLAIN " + query).consume().query_type != "r":
                    raise ValueError("Neo4j did not classify this query as read-only.")
                records = list(itertools.islice(tx.run(query), MAX_ROWS + 1))
                if len(records) > MAX_ROWS:
                    raise ValueError("Result exceeds 500 rows; add a smaller LIMIT.")
                return [record.data() for record in records]


# Initialize MCP Server
server = Server("docent-mcp-server")

@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="query_postgres",
            description="Execute a SELECT SQL query against the PostgreSQL database.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The SQL SELECT query to execute."}
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_postgres_schema",
            description="Get the list of tables and columns in the PostgreSQL database.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="query_neo4j",
            description="Execute a Cypher query against the Neo4j database.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The Cypher query to execute."}
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_neo4j_schema",
            description="Get the list of node labels and relationship types in the Neo4j database.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        )
    ]

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "query_postgres":
        try:
            query = _single_query(arguments.get("query", ""), r"^(?:SELECT|WITH)\b")
            if re.search(r"\b(?:INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|COPY|SET|RESET|set_config)\b", query, re.I):
                raise ValueError("Only read-only SQL is allowed.")
            with closing(_pg_connection()) as conn, conn:
                conn.set_session(readonly=True)
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SET LOCAL statement_timeout = '15s'")
                    cur.execute(f"SELECT * FROM ({query}) AS docent_result LIMIT {MAX_ROWS + 1}")
                    rows = cur.fetchmany(MAX_ROWS + 1)
                    if len(rows) > MAX_ROWS:
                        raise ValueError("Result exceeds 500 rows; add a smaller LIMIT.")
                    return [TextContent(type="text", text=str([dict(r) for r in rows]))]
        except Exception as e:
            return [TextContent(type="text", text=f"PostgreSQL Error: {e}")]

    elif name == "get_postgres_schema":
        query = """
            SELECT table_schema, table_name, column_name, data_type 
            FROM information_schema.columns 
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
        """
        try:
            with closing(_pg_connection()) as conn, conn:
                conn.set_session(readonly=True)
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SET LOCAL statement_timeout = '15s'")
                    cur.execute(f"SELECT * FROM ({query}) AS docent_result LIMIT {MAX_ROWS + 1}")
                    rows = cur.fetchmany(MAX_ROWS + 1)
                    if len(rows) > MAX_ROWS:
                        raise ValueError("Schema exceeds 500 columns; query information_schema with a filter.")
                    schema_info = {}
                    for r in rows:
                        tname = f"{r['table_schema']}.{r['table_name']}"
                        if tname not in schema_info:
                            schema_info[tname] = []
                        schema_info[tname].append(f"{r['column_name']} ({r['data_type']})")
                    
                    result_str = "PostgreSQL Schema:\n"
                    for t, cols in schema_info.items():
                        result_str += f"- Table: {t}\n  Columns: {', '.join(cols)}\n"
                    return [TextContent(type="text", text=result_str)]
        except Exception as e:
            return [TextContent(type="text", text=f"PostgreSQL Error: {e}")]

    elif name == "query_neo4j":
        query = arguments.get("query", "")
        try:
            records = _read_neo4j(query)
            return [TextContent(type="text", text=str(records))]
        except Exception as e:
            return [TextContent(type="text", text=f"Neo4j Error: {e}")]

    elif name == "get_neo4j_schema":
        try:
            labels = _read_neo4j("CALL db.labels()", trusted=True)
            rels = _read_neo4j("CALL db.relationshipTypes()", trusted=True)
            result_str = f"Neo4j Schema:\n- Node Labels: {labels}\n- Relationship Types: {rels}"
            return [TextContent(type="text", text=result_str)]
        except Exception as e:
            return [TextContent(type="text", text=f"Neo4j Error: {e}")]
    else:
        raise ValueError(f"Unknown tool: {name}")

async def main():
    # Run the server using stdin/stdout streams
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )

if __name__ == "__main__":
    asyncio.run(main())
