# OMEGA + FABLE Integration Plan

## Goal Description
The objective is to "marry" **Trading-OS / Fable** (a conversational, Dockerized Next.js/FastAPI stack) with **Omega** (an Apple Silicon bare-metal background agent network). 

Currently, Fable handles quick, conversational intelligence (with Redis memory and direct CLI prompt bridging). Omega handles deep, long-running agentic workflows using embedded SQLite, KuzuDB, and LanceDB. 

By integrating the two, Fable will become the **conversational front-end controller** for Omega. When a user asks for a complex research or execution task, Fable will dispatch a "goal" to Omega's Core API, retrieve a `task_id`, and allow the user to asynchronously check the status and summary of the background work.

---

> [!CAUTION]
> **User Review Required: Environment Variables & Security**
> Omega requires an `OMEGA_API_TOKEN` for any requests not originating from `localhost` loopback. Because Fable runs inside a Docker bridge network, requests to the host machine (`host.docker.internal`) will be treated as remote. We will need to set and share a secure token between both environments.

> [!IMPORTANT]
> **Open Question: Real-Time vs. Asynchronous Polling**
> Do you want Fable to automatically poll Omega in the background and interrupt the conversation when a task finishes (e.g., via Server-Sent Events or WebSockets)? Or should Fable just give you the `task_id` and rely on you to manually ask "Is it done yet?" (or check the Omega Next.js dashboard on port 3005)? The proposed plan assumes asynchronous polling (Fable checks when you ask it to).

---

## Proposed Changes

### 1. Network & Environment Layer
We need to bridge the Docker network to the macOS host to reach Omega's Core API.

#### [MODIFY] `trading-os-1/docker-compose.prod.yml`
- Inject the host alias into the API/Fable services.
- Add environment variables.

```yaml
  fable:
    environment:
      # Existing variables...
      OMEGA_API_URL: http://host.docker.internal:8003
      OMEGA_API_TOKEN: ${OMEGA_API_TOKEN}
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

#### [MODIFY] `trading-os-1/.env.prod`
```diff
+ OMEGA_API_TOKEN=your_secure_random_token_here
```

---

### 2. Fable Application Layer (Next.js)
We will add two new internal tools to Fable so that Claude (via the CLI router) can interact with Omega dynamically.

#### [MODIFY] `fable/app/api/chat/route.ts` (or equivalent tool schema file)
We will define two new functions in the system prompt / tool definitions that the Fable assistant can invoke.

**Tool 1: `dispatch_omega_task`**
- **Description:** Sends a complex, long-running goal to the background Omega agent network.
- **Parameters:** `goal` (string).
- **Implementation:** 
  ```javascript
  async function dispatchOmegaTask(goal) {
    const res = await fetch(`${process.env.OMEGA_API_URL}/v1/tasks`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${process.env.OMEGA_API_TOKEN}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ goal })
    });
    const data = await res.json();
    return `Task dispatched successfully. Task ID: ${data.task_id}`;
  }
  ```

**Tool 2: `check_omega_task`**
- **Description:** Checks the status or retrieves the final markdown summary of an Omega task.
- **Parameters:** `task_id` (string).
- **Implementation:**
  ```javascript
  async function checkOmegaTask(taskId) {
    // 1. Fetch status
    const statusRes = await fetch(`${process.env.OMEGA_API_URL}/v1/tasks/${taskId}`, ...);
    const status = await statusRes.json();
    
    if (status.state === "COMPLETED") {
       // 2. Fetch summary markdown
       const summaryRes = await fetch(`${process.env.OMEGA_API_URL}/v1/tasks/${taskId}/summary`, ...);
       return await summaryRes.text();
    }
    return `Task is currently: ${status.state}`;
  }
  ```

---

### 3. Fable System Prompt Updates
#### [MODIFY] `fable/app/api/chat/route.ts` (System Prompt Injection)
Update Fable's personality and instructions so it understands its relationship with Omega.

```diff
  You are Fable — a calm, precise, slightly witty personal finance intelligence agent.
+ You are backed by OMEGA, a heavy-duty agentic background cluster. 
+ If a user asks for deep research, comprehensive coding tasks, or anything that will take longer than a few seconds, use the `dispatch_omega_task` tool to send it to OMEGA. 
+ Do not attempt to answer deep research questions directly if OMEGA is better suited for it. Hand them the task ID, and tell them you'll keep an eye on it or they can check the dashboard.
```

---

## Verification Plan

### Automated Tests
1. Verify the network bridge: Inside the Fable container, run:
   ```bash
   docker exec trading-os-1-fable-1 curl -s -H "Authorization: Bearer $OMEGA_API_TOKEN" http://host.docker.internal:8003/v1/tasks
   ```
   This must return a `200 OK` (list of tasks), proving authentication and routing work.

### Manual Verification
1. Start Omega locally on the Mac (`make install`, export `OMEGA_API_TOKEN`, start services).
2. Open the Fable chat interface.
3. **Test 1:** Type: *"Please do a deep market analysis on TSLA and formulate a 3-day trading plan."*
   - Verify Fable responds by acknowledging it has dispatched the task to Omega, providing a Task ID.
4. **Test 2:** Type: *"What is the status of my TSLA task?"*
   - Verify Fable queries Omega, retrieves the execution state (`RUNNING` or `COMPLETED`), and seamlessly streams the result back into the chat.
