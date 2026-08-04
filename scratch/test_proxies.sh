#!/bin/bash

PROXIES=(
    "qivV9tfm:zmwK312G@138.249.139.124:62986"
    "qivV9tfm:zmwK312G@138.249.139.124:62987"
    "YNtVtMrf:NedyYDqp@138.249.223.101:64674"
    "YNtVtMrf:NedyYDqp@138.249.223.101:64675"
    "SrfSVS57:yZLP4bV6@141.133.4.2:64070"
    "SrfSVS57:yZLP4bV6@141.133.4.2:64071"
    "qivV9tfm:zmwK312G@141.133.52.25:63510"
    "qivV9tfm:zmwK312G@141.133.52.25:63511"
)

for proxy in "${PROXIES[@]}"; do
    echo "Testing socks5://$proxy"
    # Try SOCKS5
    curl -w "Time: %{time_total}s\n" --socks5 "$proxy" -sSf -m 5 https://generativelanguage.googleapis.com -o /dev/null
    
    echo "Testing http://$proxy"
    # Try HTTP
    curl -w "Time: %{time_total}s\n" -x "http://$proxy" -sSf -m 5 https://generativelanguage.googleapis.com -o /dev/null
    echo "------------------------"
done
