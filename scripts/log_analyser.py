from pathlib import Path
from collections import defaultdict

LOGS_DIR = Path("logs")

client_ips = defaultdict(int)

# for every file in directory
for log_file in LOGS_DIR.glob("*"):
    # open file
    with log_file.open("r") as file:
        # read lines
        lines = file.readlines()
        for line in lines:
            words = line.split(' ')
            if len(words) < 6:
                continue
            if f"{words[3]} {words[4]}" != "Processing request:":
                continue
            
            client_ip = words[5][7:-1]
            client_ips[client_ip] += 1

# sort by count
sorted_ips = sorted(client_ips.items(), key=lambda item: item[1], reverse=True)
# print top 10 IPs
for ip, count in sorted_ips[:10]:
    print(f"{ip}: {count} requests")
# print total number of unique IPs
print(f"Total unique IPs: {len(client_ips)}")
# print total number of requests
total_requests = sum(client_ips.values())
print(f"Total requests: {total_requests}")

