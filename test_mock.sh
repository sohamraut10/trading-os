#!/bin/bash
TOKEN="supersecret123"

QUERIES=(
  "hello fable"
  "how is the stock market today"
  "what about crypto and bitcoin"
  "analyze my portfolio"
  "is inflation going down"
  "what did the fed powell say"
  "should I buy gold"
  "what about big tech and nvidia"
  "are we in a recession"
  "thanks for the help"
)

echo "=== Fable 10-Step Mock Response Test ==="
for i in "${!QUERIES[@]}"; do
  Q="${QUERIES[$i]}"
  echo -e "\n--- Step $((i+1)) ---"
  echo "User: $Q"
  
  RES=$(docker exec trading-os-1-api-1 curl -s -X POST http://fable:3000/api/chat \
    -d "{\"message\":\"$Q\"}" \
    -H 'Content-Type: application/json' \
    -H "Authorization: Bearer $TOKEN")
  
  echo "Fable: $(echo "$RES" | grep -o '"response":"[^"]*"' | cut -d'"' -f4)"
done
