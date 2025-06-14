# app/main.py

from fastapi import FastAPI, Request
from contextlib import asynccontextmanager
from psycopg_pool import AsyncConnectionPool

from app.settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages the application's lifespan with corrected pool startup.
    """
    print("Application starting up...")
    
    # Create the pool instance
    pool = AsyncConnectionPool(conninfo=settings.DATABASE_URL, min_size=1, max_size=10)
    
    # **FIX 1: Explicitly open the pool as recommended by the warning**
    await pool.open() 
    
    app.state.pool = pool
    print("Database connection pool created and opened.")

    yield  # The application is now running

    print("Application shutting down...")
    await app.state.pool.close()
    print("Database connection pool closed.")


app = FastAPI(
    title="Theeni POS API",
    description="Backend API for the Theeni Point-of-Sale application.",
    version="0.1.0",
    lifespan=lifespan
)


@app.get("/")
def read_root():
    """A simple root endpoint to confirm the API is running."""
    return {"message": "Welcome to Theeni API!"}


@app.get("/api/v1/items")
async def get_items(request: Request):
    """
    API endpoint to fetch all items from the database.
    This is now robust and handles the case where the table is empty.
    """
    query = "SELECT id, name, quick_code, price, unit, is_discount_eligible, image_url FROM items ORDER BY name;"
    pool = request.app.state.pool

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(query)
            records = await cur.fetchall()

            # **FIX 2: Handle the case where the table is empty**
            if not records:
                return []  # Return an empty list immediately if no items are found

            # This code will now only run if records exist, preventing the error
            column_names = [desc[0] for desc in cur.description]
            items = [dict(zip(column_names, record)) for record in records]
            
            return items
