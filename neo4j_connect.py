"""
Connect to Neo4j instance (e.g. BarbaraTest) and run queries on the default database.
"""
from neo4j import GraphDatabase
from neo4j_config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE, USE_GRAPH_DEMO


def get_driver():
    """Create and return a Neo4j driver (caller should close it when done)."""
    return GraphDatabase.driver(
        NEO4J_URI,
        auth=(NEO4J_USER, NEO4J_PASSWORD),
    )


def run_query(cypher: str, parameters: dict | None = None):
    """
    Run a Cypher query on the configured Neo4j database.
    Returns list of records (each record is a dict of key -> value).
    """
    if USE_GRAPH_DEMO:
        raise RuntimeError(
            "Neo4j queries are disabled while USE_GRAPH_DEMO=1. "
            "Unset USE_GRAPH_DEMO or set it to 0 to use a real database."
        )
    params = parameters or {}
    driver = get_driver()
    try:
        with driver.session(database=NEO4J_DATABASE) as session:
            result = session.run(cypher, params)
            return [dict(record) for record in result]
    finally:
        driver.close()


def verify_connection():
    """Check connectivity and that the database is reachable."""
    if USE_GRAPH_DEMO:
        return True
    driver = get_driver()
    try:
        driver.verify_connectivity()
        with driver.session(database=NEO4J_DATABASE) as session:
            result = session.run("RETURN 1 AS ok")
            result.single()
        return True
    except Exception as e:
        raise RuntimeError(f"Neo4j connection failed: {e}") from e
    finally:
        driver.close()


if __name__ == "__main__":
    # Quick test when run as script
    try:
        verify_connection()
        print(f"Connected to Neo4j database: {NEO4J_DATABASE}")
        records = run_query("MATCH (n) RETURN count(n) AS count")
        if records:
            print(f"Node count: {records[0].get('count', 'N/A')}")
    except Exception as e:
        print(f"Error: {e}")
