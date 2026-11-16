#!/bin/bash
cd "$(dirname "$0")"; kill -TERM $(cat data/supervisor.pid) 2>/dev/null && echo stopped || echo "not running"
