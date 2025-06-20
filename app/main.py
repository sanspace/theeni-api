# app/main.py

from datetime import date, timedelta, datetime
from pydantic import BaseModel
from typing import List

from fastapi import FastAPI, Request, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from psycopg_pool import AsyncConnectionPool

from app import security
from app.settings import settings

class OrderItemCreate(BaseModel):
    id: int
    quantity: float
    price: float

class OrderCreate(BaseModel):
    cart: List[OrderItemCreate]
    discountPercentage: float
    customer_id: int | None = None

class OrderDetail(BaseModel):
    id: int
    created_at: datetime
    final_total: float
    customer_name: str | None

class OrderDetailLineItem(BaseModel):
    id: int
    item_name: str
    quantity: float
    price_per_unit: float
    subtotal: float

class OrderHistoryItem(BaseModel):
    id: int
    created_at: datetime
    final_total: float

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

class ItemUpdate(BaseModel):
    name: str
    quick_code: str | None
    price: float
    is_discount_eligible: bool
    image_url: str | None

class Customer(BaseModel):
    id: int
    name: str
    phone_number: str | None
    email: str | None

class CustomerCreate(BaseModel):
    name: str
    phone_number: str | None = None
    email: str | None = None

class CustomerReportItem(BaseModel):
    id: int
    name: str
    phone_number: str | None
    email: str | None
    total_orders: int
    total_spent: float



@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages the application's lifespan with corrected pool startup.
    """
    print("Application starting up...")
    
    # Define a dictionary for connection-specific keyword arguments
    conn_kwargs = {
        "prepare_threshold": None  # This disables server-side prepared statements
    }

    # Create the pool instance
    pool = AsyncConnectionPool(
        conninfo=settings.DATABASE_URL,
        min_size=1,
        max_size=10,
        kwargs=conn_kwargs,
    )
    
    await pool.open() 
    
    app.state.pool = pool
    print("Database connection pool created and opened.")

    yield  # The application is now running

    print("Application shutting down...")
    await app.state.pool.close()
    print("Database connection pool closed.")

origins = [origin.strip() for origin in settings.ALLOWED_ORIGINS.split(',')]


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

@app.post("/token")
async def login_for_access_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends()
):
    """
    Handles user login. Takes form data with 'username' and 'password'.
    Returns a JWT access token if credentials are correct.
    """
    user = await security.get_user(form_data.username, request)
    
    if not user or not security.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    # Create the token with user's username and role as the "subject"
    access_token = security.create_access_token(
        data={"sub": user.username, "role": user.role}
    )
    
    return {"access_token": access_token, "token_type": "bearer"}


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
async def create_order(order_data: OrderCreate, request: Request, current_user: security.UserInDB = Depends(security.get_current_user)):
    """Receives order data from the frontend and saves it to the database."""
    subtotal = sum(item.quantity * item.price for item in order_data.cart)
    pool = request.app.state.pool
    
    async with pool.connection() as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO orders (subtotal, discount_percentage, customer_id)
                    VALUES (%s, %s, %s)
                    RETURNING id;
                    """,
                    (subtotal, order_data.discountPercentage, order_data.customer_id)
                )
                order_id_record = await cur.fetchone()
                if not order_id_record:
                    raise HTTPException(status_code=500, detail="Failed to create order.")
                
                order_id = order_id_record[0]
                
                order_items_data = [
                    (order_id, item.id, item.quantity, item.price)
                    for item in order_data.cart
                ]
                
                await cur.executemany(
                    """
                    INSERT INTO order_items (order_id, item_id, quantity, price_per_unit)
                    VALUES (%s, %s, %s, %s);
                    """,
                    order_items_data
                )
                
                return {"success": True, "order_id": order_id}
            

@app.post("/api/v1/items", status_code=201)
async def add_item(
    item_data: ItemUpdate,
    request: Request,
    current_user: security.UserInDB = Depends(security.get_current_admin_user)
):
    """
    Adds a new item to the database.
    """
    pool = request.app.state.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO items (name, quick_code, price, is_discount_eligible, image_url)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (
                    item_data.name,
                    item_data.quick_code,
                    item_data.price,
                    item_data.is_discount_eligible,
                    item_data.image_url,
                )
            )
            new_item_record = await cur.fetchone()
            if not new_item_record:
                raise HTTPException(status_code=500, detail="Failed to create item.")
            
            new_id = new_item_record[0]
            return {"success": True, "item_id": new_id}


@app.put("/api/v1/items/{item_id}")
async def update_item(
    item_id: int,
    item_data: ItemUpdate,
    request: Request,
    current_user: security.UserInDB = Depends(security.get_current_admin_user),
):
    """
    Updates an existing item in the database.
    """
    pool = request.app.state.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # First, check if the item exists
            await cur.execute("SELECT id FROM items WHERE id = %s;", (item_id,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail=f"Item with id {item_id} not found.")

            # If it exists, update it
            await cur.execute(
                """
                UPDATE items
                SET name = %s,
                    quick_code = %s,
                    price = %s,
                    is_discount_eligible = %s,
                    image_url = %s
                WHERE id = %s;
                """,
                (
                    item_data.name,
                    item_data.quick_code,
                    item_data.price,
                    item_data.is_discount_eligible,
                    item_data.image_url,
                    item_id,
                )
            )
            return {"success": True, "message": f"Item {item_id} updated."}
        

@app.delete("/api/v1/items/{item_id}", status_code=200)
async def delete_item(
    item_id: int,
    request: Request,
    current_user: security.UserInDB = Depends(security.get_current_admin_user),
):
    """
    Deletes an item from the database.
    """
    pool = request.app.state.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # First, check if the item exists to provide a better error message
            await cur.execute("SELECT id FROM items WHERE id = %s;", (item_id,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail=f"Item with id {item_id} not found.")

            # If it exists, delete it
            await cur.execute("DELETE FROM items WHERE id = %s;", (item_id,))
            
            # The ON DELETE SET NULL on order_items will handle existing orders
            return {"success": True, "message": f"Item {item_id} deleted."}


@app.get("/api/v1/reports/sales", response_model=SalesReport)
async def get_sales_report(
    request: Request,
    start_date: date,
    end_date: date,
    current_user: security.UserInDB = Depends(security.get_current_admin_user),
):
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


@app.post("/api/v1/customers", response_model=Customer, status_code=201)
async def create_customer(customer_data: CustomerCreate, request: Request):
    """Creates a new customer."""
    pool = request.app.state.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO customers (name, phone_number, email) VALUES (%s, %s, %s) RETURNING id, name, phone_number, email;",
                (customer_data.name, customer_data.phone_number, customer_data.email)
            )
            new_customer_record = await cur.fetchone()
            if not new_customer_record:
                raise HTTPException(status_code=500, detail="Failed to create customer.")
            return Customer(id=new_customer_record[0], name=new_customer_record[1], phone_number=new_customer_record[2], email=new_customer_record[3])

@app.get("/api/v1/customers/search", response_model=List[Customer])
async def search_customers(request: Request, q: str):
    """Searches for customers by name, phone number, or email."""
    pool = request.app.state.pool
    search_term = f"%{q}%"
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, name, phone_number, email FROM customers WHERE name ILIKE %s OR phone_number LIKE %s OR email ILIKE %s LIMIT 10;",
                (search_term, search_term, search_term)
            )
            customer_records = await cur.fetchall()
            return [Customer(id=row[0], name=row[1], phone_number=row[2], email=row[3]) for row in customer_records]


@app.get("/api/v1/reports/orders-details", response_model=List[OrderDetail])
async def get_order_details_report(
    request: Request,
    start_date: date,
    end_date: date,
    current_user: security.UserInDB = Depends(security.get_current_admin_user)
):
    """
    Fetches a detailed list of all orders within a date range.
    """
    pool = request.app.state.pool
    end_date_exclusive = end_date + timedelta(days=1)

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT
                    o.id,
                    o.created_at,
                    o.final_total,
                    c.name as customer_name
                FROM orders o
                LEFT JOIN customers c ON o.customer_id = c.id
                WHERE o.created_at >= %s AND o.created_at < %s
                ORDER BY o.created_at DESC;
                """,
                (start_date, end_date_exclusive)
            )
            order_records = await cur.fetchall()
            return [
                OrderDetail(
                    id=row[0],
                    created_at=row[1],
                    final_total=float(row[2]),
                    customer_name=row[3]
                ) for row in order_records
            ]



@app.get("/api/v1/reports/customers", response_model=List[CustomerReportItem])
async def get_customer_report(
    request: Request,
    start_date: date | None = None,
    end_date: date | None = None,
    current_user: security.UserInDB = Depends(security.get_current_admin_user)
):
    """
    Generates a report of all customers and their order history.
    If start_date and end_date are provided, it filters stats for that period.
    """
    pool = request.app.state.pool
    
    # This subquery calculates stats for the given date range.
    # If no dates are given, it calculates lifetime stats.
    stats_subquery = """
        SELECT
            customer_id,
            COUNT(id) as total_orders,
            SUM(final_total) as total_spent
        FROM orders
    """
    params = []
    if start_date and end_date:
        end_date_exclusive = end_date + timedelta(days=1)
        stats_subquery += " WHERE created_at >= %s AND created_at < %s"
        params.extend([start_date, end_date_exclusive])
    
    stats_subquery += " GROUP BY customer_id"

    # The main query joins the customers table with the calculated stats.
    sql_query = f"""
        SELECT
            c.id, c.name, c.phone_number, c.email,
            COALESCE(stats.total_orders, 0) as total_orders,
            COALESCE(stats.total_spent, 0) as total_spent
        FROM customers c
        LEFT JOIN ({stats_subquery}) AS stats ON c.id = stats.customer_id
        ORDER BY total_spent DESC;
    """

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql_query, tuple(params))
            report_records = await cur.fetchall()
            return [
                CustomerReportItem(
                    id=row[0], name=row[1], phone_number=row[2], email=row[3],
                    total_orders=row[4], total_spent=float(row[5])
                ) for row in report_records
            ]


@app.delete("/api/v1/customers/{customer_id}", status_code=200)
async def delete_customer(
    customer_id: int,
    request: Request,
    current_user: security.UserInDB = Depends(security.get_current_admin_user)
):
    """Deletes a customer from the database. Associated orders will be kept but anonymized."""
    pool = request.app.state.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # Check if customer exists
            await cur.execute("SELECT id FROM customers WHERE id = %s;", (customer_id,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Customer not found.")
            
            # Delete the customer. The ON DELETE SET NULL on the orders table
            # will automatically handle anonymizing their past orders.
            await cur.execute("DELETE FROM customers WHERE id = %s;", (customer_id,))
            return {"success": True, "message": f"Customer {customer_id} deleted."}


@app.get("/api/v1/customers/{customer_id}/orders", response_model=List[OrderHistoryItem])
async def get_customer_orders(
    customer_id: int,
    request: Request,
    current_user: security.UserInDB = Depends(security.get_current_admin_user)
):
    """Fetches all past orders for a specific customer."""
    pool = request.app.state.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, created_at, final_total FROM orders
                WHERE customer_id = %s
                ORDER BY created_at DESC;
                """,
                (customer_id,)
            )
            order_records = await cur.fetchall()
            return [
                OrderHistoryItem(id=row[0], created_at=row[1], final_total=float(row[2]))
                for row in order_records
            ]

@app.get("/api/v1/orders/{order_id}/items", response_model=List[OrderDetailLineItem])
async def get_order_line_items(
    order_id: int,
    request: Request,
    current_user: security.UserInDB = Depends(security.get_current_admin_user)
):
    """Fetches all line items for a specific order."""
    pool = request.app.state.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # This query joins order_items with items to get the product name
            await cur.execute(
                """
                SELECT
                    oi.id,
                    i.name as item_name,
                    oi.quantity,
                    oi.price_per_unit,
                    oi.subtotal
                FROM order_items oi
                JOIN items i ON oi.item_id = i.id
                WHERE oi.order_id = %s;
                """,
                (order_id,)
            )
            line_items = await cur.fetchall()
            return [
                OrderDetailLineItem(
                    id=row[0],
                    item_name=row[1],
                    quantity=float(row[2]),
                    price_per_unit=float(row[3]),
                    subtotal=float(row[4])
                ) for row in line_items
            ]
