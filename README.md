# 🚀 Agentic Integration Platform

> Enterprise-grade AI-powered integration platform that transforms natural language requirements into production-ready integration code.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green.svg)](https://fastapi.tiangolo.com/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## ✨ Features

- **🤖 AI-Powered Code Generation**: Generate production-ready integration code from natural language specifications
- **🧠 Knowledge Graph Integration**: Semantic understanding of systems, entities, and relationships using Neo4j
- **💬 Model Context Protocol (MCP)**: Persistent conversational context across integration sessions
- **🔄 Multi-Provider AI Support**: Anthropic Claude, OpenAI GPT, and extensible architecture
- **🔍 Semantic Validation**: AI-powered code quality, security, and performance analysis
- **📚 Pattern Learning**: Learn from successful integrations to improve future generations
- **🏭 Production Ready**: Comprehensive logging, monitoring, error handling, and security
- **🔌 Extensible Architecture**: Plugin system for new AI providers and integration types
- **🌐 RESTful API**: FastAPI with automatic OpenAPI documentation
- **🔒 Security First**: Built-in security scanning, authentication, and authorization

## 🚀 Quick Start

### Option 1: Smart Installer (Recommended)
```bash
# Clone the repository
git clone <repository-url>
cd agentic-integration-platform

# Install with smart dependency management
make install

# Configure environment
cp .env.example .env
# Edit .env with your API keys (see Configuration section)

# Start development server
make dev

# Visit API documentation
open http://localhost:8000/docs
```

### Option 2: Manual Installation
```bash
# Install dependencies without ML features (faster)
make install-no-root

# Or install with ML features (PyTorch, sentence-transformers)
make install-ml
```

## 🏗️ Architecture

The platform implements a layered architecture with:

- **🌐 API Layer**: FastAPI with async support and automatic documentation
- **⚙️ Service Layer**: Business logic, AI orchestration, and integration management
- **💾 Data Layer**: PostgreSQL, Neo4j (knowledge graph), Redis (caching), Qdrant (vectors)
- **🤖 AI Layer**: Multi-provider LLM integration with prompt management
- **🧠 Knowledge Layer**: Graph-based semantic understanding and pattern learning
- **💬 MCP Layer**: Conversational context management and session persistence

## 📋 Prerequisites

### Required
- **Python 3.12+**
- **Poetry** (for dependency management)

### Optional (for full functionality)
- **PostgreSQL** (database)
- **Redis** (caching)
- **Neo4j** (knowledge graph)
- **Qdrant** (vector search)

## 🛠️ Installation Guide

### Step 1: Install Dependencies

```bash
# Smart installer (handles PyTorch compatibility issues)
make install

# When prompted about ML features:
# - Choose 'N' for quick setup (recommended)
# - Choose 'Y' only if you need local embeddings
```

### Step 2: Configure Environment

```bash
# Copy example environment file
cp .env.example .env

# Edit with your configuration
nano .env  # or code .env, vim .env, etc.
```

**Essential Configuration:**
```env
# AI Services (add at least one)
ANTHROPIC_API_KEY=your_anthropic_key_here
OPENAI_API_KEY=your_openai_key_here

# Default AI settings
DEFAULT_LLM_PROVIDER=anthropic
DEFAULT_MODEL=claude-3-5-sonnet-20241022

# Database (required)
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/agentic_integration

# Security (required)
SECRET_KEY=your-super-secret-key-here
JWT_SECRET_KEY=another-secret-key-here

# Optional services
REDIS_URL=redis://localhost:6379/0
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
QDRANT_HOST=localhost
QDRANT_PORT=6333
```

### Step 3: Set Up External Services (Optional)

#### PostgreSQL (Recommended)
```bash
# Using Docker
docker run --name postgres -e POSTGRES_PASSWORD=postgres -p 5432:5432 -d postgres:15

# Or install locally on macOS
brew install postgresql
brew services start postgresql
```

#### Neo4j (For Knowledge Graph)
```bash
# Using Docker
docker run --name neo4j -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/password -d neo4j:latest

# Access Neo4j Browser at http://localhost:7474
```

#### Qdrant (For Vector Search)
```bash
# Using Docker
docker run --name qdrant -p 6333:6333 -d qdrant/qdrant
```

#### Redis (For Caching)
```bash
# Using Docker
docker run --name redis -p 6379:6379 -d redis:alpine

# Or install locally on macOS
brew install redis
brew services start redis
```

### Step 4: Start the Platform

```bash
# Start development server
make dev

# The server will start at http://localhost:8000
```

### Step 5: Explore the Platform

- 🌐 **API Documentation**: http://localhost:8000/docs
- 🔍 **Health Check**: http://localhost:8000/health
- 📊 **Metrics**: http://localhost:8000/metrics
- 🧠 **Neo4j Browser**: http://localhost:7474 (if Neo4j is running)

## ⚙️ Configuration Reference

### Core Settings
```env
# Application
APP_NAME=Agentic Integration Platform
DEBUG=true
LOG_LEVEL=INFO

# Database
DATABASE_URL=postgresql+asyncpg://user:pass@host:port/dbname

# AI Providers
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
DEFAULT_LLM_PROVIDER=anthropic  # or openai
DEFAULT_MODEL=claude-3-5-sonnet-20241022

# Security
SECRET_KEY=your-secret-key
JWT_SECRET_KEY=your-jwt-secret
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

# External Services
REDIS_URL=redis://localhost:6379/0
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
QDRANT_HOST=localhost
QDRANT_PORT=6333

# Embeddings
EMBEDDING_MODEL=all-MiniLM-L6-v2
VECTOR_DIMENSION=384
USE_OPENAI_EMBEDDINGS=false  # Set to true if no local ML
```

## 🔧 Development

### Available Commands
```bash
# Installation
make install              # Smart installer
make install-no-root      # Install without current project
make install-ml           # Install with ML dependencies

# Development
make dev                  # Start development server
make dev-reload           # Start with auto-reload
make shell               # Interactive Python shell

# Code Quality
make format              # Format code with Black
make lint                # Lint with Ruff
make type-check          # Type checking with MyPy
make security-check      # Security scan with Bandit

# Testing
make test                # Run all tests
make test-unit           # Run unit tests only
make test-integration    # Run integration tests
make test-coverage       # Run tests with coverage

# Database
make db-upgrade          # Run database migrations
make db-downgrade        # Rollback migrations
make db-reset            # Reset database

# Docker
make docker-build        # Build Docker image
make docker-run          # Run in Docker
make docker-compose-up   # Start all services
```

### Project Structure
```
agentic-integration-platform/
├── app/                          # Main application code
│   ├── api/                      # FastAPI routes and endpoints
│   ├── core/                     # Core utilities and configuration
│   ├── models/                   # SQLAlchemy database models
│   ├── services/                 # Business logic services
│   │   ├── ai/                   # AI service integrations
│   │   ├── codegen/              # Code generation engine
│   │   ├── knowledge/            # Knowledge graph services
│   │   └── mcp/                  # Model Context Protocol
│   └── database/                 # Database configuration
├── alembic/                      # Database migrations
├── tests/                        # Test suite
├── docker/                       # Docker configurations
├── docs/                         # Documentation
└── scripts/                      # Utility scripts
```

## 🚀 Deployment

### Docker Deployment
```bash
# Build production image
make docker-build

# Run with Docker Compose
make docker-compose-up

# Or deploy to production
make deploy-production
```

### Environment-Specific Deployment
```bash
# Development
make deploy-dev

# Staging
make deploy-staging

# Production
make deploy-production
```

## 🧪 Testing

```bash
# Run all tests
make test

# Run specific test categories
make test-unit
make test-integration
make test-e2e

# Run with coverage
make test-coverage

# Run specific test file
poetry run pytest tests/test_specific.py -v
```

## 📚 Usage Examples

1. **Initial Conversation**

    ```python
    from app.services.mcp import ConversationService

    conversation_service = ConversationService()
    conversation = await conversation_service.create_conversation(
        db=db,
        title="Integration Planning Session",
    )
    response = await conversation_service.generate_response(
        db=db,
        conversation_id=conversation.id,
        user_message="I need to integrate our CRM with our email marketing platform",
    )
    print(response.content)  # AI response with integration recommendations
    ```

2. **Knowledge Graph Queries**

    ```python
    from app.services.knowledge import EntityService
    from app.models.knowledge import EntityType  # import the enumeration

    entity_service = EntityService()
    entities = await entity_service.search_entities(
        db=db,
        query="customer data synchronization",
        entity_type=EntityType.BUSINESS_OBJECT,
    )
    for entity in entities:
        print(f"Found: {entity.name} – {entity.description}")
    ```

3. **Generate Integration Code**

    ```python
    from app.services.codegen import CodeGenerator
    from app.models.integration import IntegrationType  # import the enumeration

    generator = CodeGenerator()
    result = await generator.generate_integration_code(
        specification="Sync customer data from Salesforce to HubSpot when accounts are created",
        integration_type=IntegrationType.SYNC,
        source_system={"name": "Salesforce", "type": "crm"},
        target_system={"name": "HubSpot", "type": "crm"},
    )
    print(result["code"])  # Generated Python integration code
    ```

## 🔍 Troubleshooting

### Common Issues

#### 1. PyTorch Installation Fails
```bash
# Use the no-ML installation
make install-no-root

# Configure to use OpenAI embeddings instead
echo "USE_OPENAI_EMBEDDINGS=true" >> .env
```

#### 2. Database Connection Issues
```bash
# Check PostgreSQL is running
brew services list | grep postgresql

# Reset database
make db-reset
```

#### 3. Poetry Dependency Conflicts
```bash
# Clear Poetry cache
poetry cache clear pypi --all

# Remove lock file and reinstall
rm poetry.lock
poetry install --no-cache
```

#### 4. Port Already in Use
```bash
# Find process using port 8000
lsof -i :8000

# Kill the process
kill -9 <PID>

# Or use a different port
export PORT=8001
make dev
```

### Getting Help

1. **Check the logs**: `tail -f logs/app.log`
2. **Run health check**: `curl http://localhost:8000/health`
3. **Check API docs**: http://localhost:8000/docs
4. **Validate configuration**: `make validate-config`

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Make your changes
4. Run tests: `make test`
5. Commit changes: `git commit -m 'Add amazing feature'`
6. Push to branch: `git push origin feature/amazing-feature`
7. Open a Pull Request

## Third‑Party Services and Dependencies

This project is released under the MIT Licence
. The code in this repository is permissively licensed, but it relies on several external services and open‑source components that have their own licences and terms of use. When deploying or extending this platform, you are responsible for complying with the terms of these providers:

Anthropic – Used for Claude‑family LLMs to generate responses and integration code. Access requires an Anthropic API key and agreement to Anthropic’s service terms.

OpenAI – Used for GPT models and embedding services. You’ll need an OpenAI API key and must adhere to OpenAI’s usage policies.

Neo4j – Provides the graph database backend for storing and querying the knowledge graph. Neo4j is distributed under the GPLv3 licence; see Neo4j licensing
 for details.

FastAPI – The web server framework powering the API layer. FastAPI is released under the MIT licence.

Qdrant – Supplies vector storage and similarity search capabilities. Qdrant is distributed under the Apache 2.0 licence.

These external tools are not bundled with this repository; you will need to provision access and API keys as appropriate. Your rights and obligations with respect to these services are determined by their individual licences and terms of use, not by this project’s MIT licence.

## 📄 License

MIT License - see [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **Anthropic** for Claude AI models
- **OpenAI** for GPT models and embeddings
- **Neo4j** for graph database technology
- **FastAPI** for the excellent web framework
- **Qdrant** for vector search capabilities

---

**Built with ❤️ by Shiv Yadav**
