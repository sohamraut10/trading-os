export function connectWS(onMessage, onStatusChange) {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${protocol}//${window.location.host}/ws/events`;
  let socket = null;
  let backoff = 1000;

  function connect() {
    onStatusChange("connecting");
    socket = new WebSocket(url);

    socket.onopen = () => {
      onStatusChange("connected");
      backoff = 1000;
    };

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        onMessage(data);
      } catch (err) {
        console.error("WebSocket message parsing error:", err);
      }
    };

    socket.onclose = () => {
      onStatusChange("disconnected");
      setTimeout(() => {
        backoff = Math.min(backoff * 2, 30000);
        connect();
      }, backoff);
    };

    socket.onerror = (err) => {
      console.error("WebSocket error:", err);
      socket.close();
    };
  }

  connect();

  return {
    close: () => {
      if (socket) {
        socket.onclose = null;
        socket.close();
      }
    },
  };
}
