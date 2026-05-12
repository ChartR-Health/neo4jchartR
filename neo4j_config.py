"""
Neo4j connection config for instance BarbaraTest.
Connects to the default database "neo4j" on that instance.
Set NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD in environment or .env file.
"""
import os

# Optional: load .env file if python-dotenv is installed
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

# When true, read APIs use in-memory sample data (graph_demo_data) and never open the Neo4j driver.
USE_GRAPH_DEMO = os.getenv("USE_GRAPH_DEMO", "").strip().lower() in ("1", "true", "yes", "on")
