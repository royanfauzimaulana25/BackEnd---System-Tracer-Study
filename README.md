# FastAPI Project Template

A comprehensive template for building RESTful APIs with FastAPI.

## Features

- **Modern Python FastAPI framework**
- **Structured layout for medium to large applications**
- **SQLAlchemy ORM integration**
- **Pydantic data validation**
- **JWT authentication**
- **Alembic database migrations support**
- **Pytest for unit and integration tests**
- **Docker support**

## Project Structure
# Tracer Study SMA API

A FastAPI application for tracking high school alumni information and education paths.

## Features

- Alumni verification and registration
- Tracer study form submission with document upload
- Statistics on alumni education paths
- Reference data APIs for dropdowns and form options
- Authentication system for admin access

## Project Structure

The project follows a modular structure for maintainability:

- `app/` - Main application package
  - `core/` - Core configuration and utilities
  - `db/` - Database connection management
  - `models/` - Pydantic models for request/response validation
  - `repositories/` - Database operation classes
  - `routers/` - API route handlers
  - `main.py` - FastAPI application definition

## Installation

1. Clone the repository
2. Create a virtual environment:
   ```
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
4. Create a `.env` file based on `.env.example`

## Running the Application