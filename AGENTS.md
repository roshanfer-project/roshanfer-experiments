# Overview

The main goal is fast prototyping. 


# Coding style and guidelines

- Minimal and simple implementations (fewer abstractions, prioritizing understandibity).
- Re-use the existing codebase when possible.
- DO NOT WRITE VERBOSE COMMENTS. keep them short, simple, and to the point.


# Guidlines for speciifc scenarios

### Debugging

When the prompt is about fixing and issue or bug, you should generally apply very few changes to fix the bug and avoid chainging the strucutres, abstractions, etc. If you have to make a lot of changes, consult your plan with me before applying changes.

### Implementing new features or extneding existing ones

In these scenarios, you should always reuse as much as code possible from the existing codebase.


### When the prompt is asking a question

In these scenarios, try to teach the broader concept that lead to the question. For example, if the question is about why a particalur type casting in C++ doesn't work, you should also include a brief note about how that particular casting works.

# Input and Context

- user prompt


# Output and Planning

The agent should work in two phases: planning and execution.

## Planning phase
The agent consumes all the context and comes up with a plan to fulfill user's request.
The agent should present the summary of the plan along with a summary of the context consumed (including a confirmation that it has read this file) to the user for confirmation.
If the agent is uncertain about parts of the plan, it should ask its questions in the planning phase. DO NOT WRITE QUESTIONS IN THE CODE. If you are uncertain, ask me directly in the planning phase.

The agent should repeat this phase until its certain.


## Execution phase
The agent simply executes the agreed plan.

At the end, it should present a summary of changes to the user.

Always update the related REASME files in directories to reflect the new changes. Keep it simple and concise.