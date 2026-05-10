.PHONY: install run dev test clean lint format

# Install dependencies
install:
	pip install -r requirements.txt

# Run production server
run:
	python app.py

# Run development server with debug
dev:
	set FLASK_ENV=development && set FLASK_DEBUG=1 && python app.py

# Run tests
test:
	python -m pytest tests/ -v

# Clean generated files
clean:
	del /s /q static\output\*.png
	del /s /q static\output\*.mp4
	del /s /q static\output\*.json
	del /s /q static\output\*.zip
	del /s /q logs\*.log

# Lint code
lint:
	python -m flake8 --max-line-length=120 app.py logic.py utils.py config.py

# Format code
format:
	python -m black app.py logic.py utils.py config.py

# Create virtual environment
venv:
	python -m venv venv
	venv\Scripts\activate

# Update requirements
freeze:
	pip freeze > requirements.txt

# Run with gunicorn (production - Linux/Mac)
prod:
	gunicorn -w 4 -b 0.0.0.0:5000 app:app

# Health check
health:
	curl http://localhost:5000/health

# Show stats
stats:
	curl http://localhost:5000/stats
