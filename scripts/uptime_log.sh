#!/bin/bash

# Define the log file
NOW=$(date +"%Y%m%d-%H%M%S")
LOG_FILE="/home/$USER/uplog.$NOW"

# Create the log file if it doesn't exist and add a header
echo "Uptime log started at $NOW" > "$LOG_FILE"

# Loop indefinitely
while true
do
  # Get the current uptime and append it to the log file with timestamp
  echo "$(date) - $(uptime)" >> "$LOG_FILE"
  
  # Wait for 5 seconds before running again
  sleep 5
done
