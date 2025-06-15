# create_admin.py
import os
import psycopg
from getpass import getpass
from passlib.context import CryptContext
from dotenv import load_dotenv

# Load the database URL from your .env file
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

# Create a password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def create_admin_user():
    """A command-line script to create the first admin user."""
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not found in .env file.")
        return

    try:
        print("--- Create Theeni Admin User ---")
        username = input("Enter admin username: ")
        password = getpass("Enter admin password: ")
        password_confirm = getpass("Confirm admin password: ")

        if password != password_confirm:
            print("Passwords do not match. Aborting.")
            return

        # Hash the password
        hashed_password = pwd_context.hash(password)
        
        # Connect to the database and insert the new user with the 'admin' role
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                # UPDATED: Added the 'role' column to the insert query
                cur.execute(
                    "INSERT INTO users (username, hashed_password, role) VALUES (%s, %s, %s)",
                    (username, hashed_password, 'admin') # Hardcoding the role to 'admin'
                )
                conn.commit()
        
        print(f"\n✅ Admin user '{username}' created successfully with the 'admin' role!")

    except Exception as e:
        print(f"\n❌ An error occurred: {e}")

if __name__ == "__main__":
    create_admin_user()
