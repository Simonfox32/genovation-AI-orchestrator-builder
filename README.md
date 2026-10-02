AI Build Router
Overview
AI Build Router is a system designed to take a product idea, break it into development work, and route that work to AI development tools based on the requirements of each task.
Our capstone project explores an extension to the existing AI Build Router architecture using role-based agent orchestration. Instead of relying only on module decomposition and tool routing, the proposed design investigates how specialized agent roles could coordinate development work while continuing to use the Build Router's existing routing system underneath.
Project Goals
The project will investigate how agent orchestration can be integrated into the existing architecture without replacing its core components. Areas of research include:
- Defining useful agent roles and their responsibilities
- Designing workflows and handoffs between agent roles
- Coordinating changes between agents working on dependent components
- Maintaining shared context as work moves between agents
- Integrating agent-created tasks with the existing AI tool-routing system
- Preserving the Build Router's existing validation, rerouting, and artifact-management systems
Proposed Architecture
The existing Build Router follows a general workflow of:
Product Request
      ↓
Requirement Parser / Product Analyzer
      ↓
Project Graph / Module Decomposer
      ↓
Routing Engine
      ↓
Task Orchestrator
      ↓
AI Tool Adapters
      ↓
Artifact Registry / Shared Contract
      ↓
Assembly & Validation
      ↓
Approval & Deployment

Our research explores adding role-based development coordination to this architecture.
Agent roles determine what work needs to be performed and how development tasks interact, while the existing Routing Engine remains responsible for determining which AI tool should execute the work.
Agent Role
    ↓
Development Task
    ↓
Existing Routing Engine
    ↓
Selected AI Tool
    ↓
Generated Artifact
    ↓
Review / Validation
    ↓
Agent Workflow Continues

Sprint 2
Sprint 2 focuses on research and architecture design, not implementation.
The sprint includes six areas of work:
1. Compare Orchestration Approaches — Compare the existing Build Router architecture with the proposed role-based orchestration approach.
2. Research Possible Agent Roles — Evaluate possible planning, development, review, testing, and debugging roles.
3. Design Agent Workflow — Define how work moves between agent roles and how revisions are handled.
4. Design Coordination Between Agent Roles — Research how agents working on dependent components should request and coordinate changes.
5. Define Shared Context Between Agent Roles — Determine what project and task information needs to move between agents.
6. Integrate Agent Roles with Tool Routing — Research how tasks produced by agent roles could interact with the existing module-based Routing Engine.
Team
- Simon Fox
- Aaron Romero
- Anula Dinesh
- Fill Sanpanawat
Current Status
The project is currently in the research and design phase. Sprint 2 will produce design documentation and architecture decisions that can be reviewed with the project sponsor before implementation begins.
