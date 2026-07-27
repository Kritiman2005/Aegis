#!/bin/bash
echo "Starting Aegis backend..."
uvicorn main:app --ws-ping-interval 60 --ws-ping-timeout 120
