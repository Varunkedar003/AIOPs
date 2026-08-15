# AIOps Commander

An AI-powered Cloud Operations Platform for intelligent monitoring, management, and automation of cloud infrastructure.

## Overview

AIOps Commander is a production-quality platform that leverages AI to provide intelligent insights and automation for cloud operations. It integrates with major cloud providers, monitoring systems, and development tools to deliver a unified operations experience.

## Current Status: Phase 1 - Interactive Infrastructure Explorer

The project is currently in Phase 1, featuring a fully functional interactive infrastructure explorer with:

- **Azure Subscription Explorer**: Hierarchical resource navigation with search and health indicators
- **Resource Details Panel**: Comprehensive resource information display
- **Interactive Topology Graph**: Visual infrastructure dependency mapping with zoom, pan, and click navigation
- **Service Layer**: Clean architecture with business logic separation
- **Provider Abstraction**: Empty placeholder providers, ready for live Azure SDK/GitLab integration (Phase 2)

## Architecture

The project follows a clean architecture with clear separation of concerns:

- **Dashboard**: Streamlit-based UI for visualization and interaction
- **Services**: Business logic layer (ResourceService, GraphService)
- **Providers**: Provider interfaces with empty placeholder implementations, pending live Azure/GitLab integration
- **Workflow**: LangGraph-based orchestration of AI workflows (future)
- **Agents**: Specialized AI agents for different domains (future)
- **Tools**: Domain-specific tools for cloud operations (future)
- **LLM**: Language model integration (Ollama, Anthropic Claude) (future)
- **Utils**: Shared utilities and helper functions

## Tech Stack

- **Backend**: Python 3.10+
- **UI Framework**: Streamlit
- **Graph Visualization**: streamlit-agraph
- **AI Orchestration**: LangGraph (future)
- **AI Agents**: CrewAI (future)
- **Local LLM**: Ollama (future)
- **Cloud Provider**: Azure (future phases)
- **Container Orchestration**: AKS (future phases)
- **Version Control**: GitLab (future phases)
- **Monitoring**: Azure Monitor, Application Insights, Log Analytics (future phases)

## Installation

```bash
# Navigate to project directory
cd S:\aiops_commander

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy environment variables
cp .env.example .env
# Edit .env with your configuration
```

## Running the Application

```bash
# From the project directory
streamlit run app.py
```

The application will launch in your browser at `http://localhost:8501`

## Development Phases

### ✅ Phase 1: Local Foundation (Completed)
- ✅ Project structure and configuration
- ✅ Empty placeholder providers, ready for live data
- ✅ Interactive Streamlit dashboard
- ✅ Service layer architecture
- ✅ Infrastructure topology graph
- ✅ Resource details panel
- ✅ Navigation controls

### 🔄 Phase 2: Local LLM Integration (Next)
- Ollama integration for local inference
- Basic agent framework
- Simple workflow orchestration
- AI chat interface

### 📋 Phase 3: Cloud Provider Integration
- Azure SDK integration
- AKS cluster management
- Real provider implementations

### 📋 Phase 4: Observability Integration
- Azure Monitor integration
- Application Insights integration
- Log Analytics integration

### 📋 Phase 5: GitLab Integration
- GitLab API integration
- CI/CD pipeline monitoring
- Repository management

### 📋 Phase 6: Advanced AI Features
- CrewAI agent orchestration
- Complex multi-agent workflows
- Anthropic Claude integration

## Project Structure

```
aiops_commander/
├── app.py              # Main Streamlit application entry point
├── config.py           # Application configuration
├── requirements.txt    # Python dependencies
├── README.md          # This file
├── .env.example       # Environment variable template
├── dashboard/         # Streamlit UI components
│   ├── __init__.py
│   ├── sidebar.py      # Azure Subscription Explorer
│   ├── topology.py     # Infrastructure Topology graph
│   ├── details.py      # Resource Details panel
│   ├── chat.py         # AI Investigation panel
│   └── graph_visualization.py  # Graph rendering
├── services/          # Business logic layer
│   ├── __init__.py
│   ├── resource_service.py  # Resource management
│   └── graph_service.py     # Topology graph generation
├── providers/         # Cloud provider interfaces
│   ├── __init__.py
│   ├── base_provider.py      # Base provider class
│   ├── azure_provider.py     # Azure resource provider (empty placeholder)
│   ├── aks_provider.py       # Kubernetes provider (empty placeholder)
│   ├── gitlab_provider.py    # GitLab CI/CD provider (empty placeholder)
│   ├── observability_provider.py  # Monitoring provider (empty placeholder)
│   └── cost_provider.py      # Cost analysis provider (empty placeholder)
├── workflow/          # LangGraph workflow definitions (future)
├── agents/            # AI agent implementations (future)
├── tools/             # Domain-specific tools (future)
├── llm/              # LLM integration (Ollama, Claude) (future)
├── graph/            # Workflow graph definitions (future)
├── assets/           # Static assets (images, etc.)
├── tests/            # Test suite
└── utils/            # Shared utilities
```

## Features

### Current Features (Phase 1)
- **Interactive Resource Explorer**: Browse Azure resources by type with search functionality
- **Health Status Indicators**: Visual indicators for resource health (🟢 Healthy, 🟡 Warning, 🔴 Critical)
- **Resource Details**: Comprehensive information display including tags, dependencies, and timestamps
- **Topology Graph**: Interactive visualization of infrastructure dependencies
- **Navigation Controls**: Back/Forward navigation through resource selection history
- **Clean Architecture**: Service layer separation ready for real API integration

### Planned Features
- AI-powered resource analysis and recommendations
- Real-time monitoring and alerting
- Cost optimization insights
- Automated incident response
- Multi-cloud support

## License

Proprietary - All rights reserved
