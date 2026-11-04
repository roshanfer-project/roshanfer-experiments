# Overview

The main goal is fast prototyping. 


# Coding style and guidelines

- Minimal and simple implementations (fewer abstractions, prioritizing understandability).
- Re-use the existing codebase when possible.
- DO NOT WRITE VERBOSE COMMENTS. Keep them short, simple, and to the point.


# Guidelines for specific scenarios

### Debugging

When the prompt is about fixing an issue or bug, you should generally apply very few changes to fix the bug and avoid changing the structures, abstractions, etc. If you have to make a lot of changes, consult your plan with me before applying changes.

### Implementing new features or extending existing ones

In these scenarios, you should always reuse as much code as possible from the existing codebase.


### When the prompt is asking a question

In these scenarios, try to teach the broader concept that leads to the question. For example, if the question is about why a particular type casting in C++ doesn't work, you should also include a brief note about how that particular casting works.

# Input and Context

- user prompt


# Output and Planning

The agent should work in two phases: planning and execution.

## Planning phase
The agent consumes all the context and comes up with a plan to fulfill the user's request.
The agent should present the summary of the plan along with a summary of the context consumed (including a confirmation that it has read this file) to the user for confirmation.
If the agent is uncertain about parts of the plan, it should ask its questions in the planning phase. DO NOT WRITE QUESTIONS IN THE CODE. If you are uncertain, ask me directly in the planning phase.

The agent should repeat this phase until it is certain.


## Execution phase
The agent simply executes the agreed plan.

At the end, it should present a summary of changes to the user.
