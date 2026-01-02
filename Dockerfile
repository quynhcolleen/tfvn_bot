# Use Python 3.11 slim image for a lightweight base
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Copy requirements.txt first for better caching (if it exists)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project (including .env, data/, cogs/, etc.)
COPY . .

# Set environment variables (override with docker run -e if needed)
# Note: In production, use Docker secrets or env vars for sensitive data like tokens
ENV ENVIRONMENT=production

# Expose no ports (Discord bots connect outbound)

# Command to run the bot
CMD ["python", "main.py"]