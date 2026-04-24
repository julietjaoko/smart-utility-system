#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e 

echo "Installing dependencies..."
pip install -r requirements.txt

echo "Running database migrations..."
python manage.py migrate --noinput

echo "Build complete."