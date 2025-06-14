# app/main.py

from datetime import date, timedelta
from pydantic import BaseModel
from typing import List

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from psycopg_pool import AsyncConnectionPool

from app.settings import settings

class OrderItemCreate(BaseModel):
    id: int
    quantity: float
    price: float

class OrderCreate(BaseModel):
    cart: List[OrderItemCreate]
    discountPercentage: float

class ReportSalesByItem(BaseModel):
    id: int
    name: str
    total_quantity_sold: float
    total_revenue_from_item: float

class ReportSummary(BaseModel):
    total_revenue: float
    total_orders: int
    total_discount_given: float

class SalesReport(BaseModel):
    summary: ReportSummary
    sales_by_item: List[ReportSalesByItem]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages the application's lifespan with corrected pool startup.
    """
    print("Application starting up...")
    
    # Create the pool instance
    pool = AsyncConnectionPool(conninfo=settings.DATABASE_URL, min_size=1, max_size=10)
    
    await pool.open() 
    
    app.state.pool = pool
    print("Database connection pool created and opened.")

    yield  # The application is now running

    print("Application shutting down...")
    await app.state.pool.close()
    print("Database connection pool closed.")

origins = [
    "http://localhost:5173", # The default Vite dev server port
    "http://127.0.0.1:5173",
]

app = FastAPI(
    title="Theeni POS API",
    description="Backend API for the Theeni Point-of-Sale application.",
    version="0.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"], # Allows all methods (GET, POST, etc.)
    allow_headers=["*"], # Allows all headers
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

            if not records:
                return []  # Return an empty list immediately if no items are found

            # This code will now only run if records exist, preventing the error
            column_names = [desc[0] for desc in cur.description]
            items = [dict(zip(column_names, record)) for record in records]
            
            return items


@app.post("/api/v1/orders")
async def create_order(order_data: OrderCreate, request: Request):
    """
    Receives order data from the frontend and saves it to the database.
    """
    # CRITICAL: Always recalculate totals on the backend for security and accuracy.
    subtotal = sum(item.quantity * item.price for item in order_data.cart)
    
    pool = request.app.state.pool
    
    # Use a transaction to ensure all queries succeed or none do.
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                # 1. Insert into the 'orders' table
                await cur.execute(
                    """
                    INSERT INTO orders (subtotal, discount_percentage)
                    VALUES (%s, %s)
                    RETURNING id;
                    """,
                    (subtotal, order_data.discountPercentage)
                )
                
                order_id_record = await cur.fetchone()
                if not order_id_record:
                    # This would be a server error, FastAPI handles the response
                    raise HTTPException(status_code=500, detail="Failed to create order.")
                
                order_id = order_id_record[0]
                
                # 2. Prepare and insert all items into the 'order_items' table
                order_items_data = [
                    (order_id, item.id, item.quantity, item.price)
                    for item in order_data.cart
                ]
                
                # Use execute_many for efficient bulk insertion
                await cur.executemany(
                    """
                    INSERT INTO order_items (order_id, item_id, quantity, price_per_unit)
                    VALUES (%s, %s, %s, %s);
                    """,
                    order_items_data
                )
                
                return {"success": True, "order_id": order_id}


@app.get("/api/v1/reports/sales", response_model=SalesReport)
async def get_sales_report(request: Request, start_date: date, end_date: date):
    """
    Generates a sales report for a given date range.
    The end_date is exclusive (e.g., to get a full day, start_date=today, end_date=tomorrow).
    """
    pool = request.app.state.pool
    # Adjust end_date to be exclusive for 'less than' comparison
    end_date_exclusive = end_date + timedelta(days=1)
    
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 1. Get the summary metrics
            await cur.execute(
                """
                SELECT
                    COALESCE(SUM(final_total), 0) as total_revenue,
                    COUNT(id) as total_orders,
                    COALESCE(SUM(discount_amount), 0) as total_discount_given
                FROM orders
                WHERE created_at >= %s AND created_at < %s;
                """,
                (start_date, end_date_exclusive),
            )
            summary_data = await cur.fetchone()
            summary = ReportSummary(
                total_revenue=summary_data[0], 
                total_orders=summary_data[1], 
                total_discount_given=summary_data[2]
            )

            # 2. Get the sales breakdown by item
            await cur.execute(
                """
                SELECT
                    i.id,
                    i.name,
                    SUM(oi.quantity) as total_quantity_sold,
                    SUM(oi.subtotal) as total_revenue_from_item
                FROM order_items oi
                JOIN items i ON oi.item_id = i.id
                JOIN orders o ON oi.order_id = o.id
                WHERE o.created_at >= %s AND o.created_at < %s
                GROUP BY i.id, i.name
                ORDER BY total_revenue_from_item DESC;
                """,
                (start_date, end_date_exclusive),
            )
            sales_by_item_data = await cur.fetchall()
            sales_by_item = [
                ReportSalesByItem(
                    id=row[0], 
                    name=row[1], 
                    total_quantity_sold=row[2], 
                    total_revenue_from_item=row[3]
                ) for row in sales_by_item_data
            ]
            
            return SalesReport(summary=summary, sales_by_item=sales_by_item)
