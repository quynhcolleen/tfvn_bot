import os
from pymongo import MongoClient 
from dotenv import load_dotenv

load_dotenv()

# Build the connection string from .env variables
connection_string = f"{os.getenv('DB_METHOD')}://{os.getenv('DB_USERNAME')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_HOST')}/?retryWrites=true&w=majority"

# Create a global MongoDB client
client = MongoClient(connection_string)

# Access the database (replace 'your_db_name' with your actual database name)
db = client[os.getenv('DB_NAME')]