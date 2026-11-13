---
tags: [skill-creator]
max_turns: 20
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

LangGraph 에이전트에 HITL을 넣으려고 해. 사용자 질문이 모호하면(예: '뭐부터 해야 돼?') interrupt()로 멈춰서 되묻고, 사용자가 답하면 resume하는 식으로 하려는데 이 설계 괜찮아? 웹 채팅 화면도 있어서 승인 버튼 같은 것도 붙일 생각이야.
