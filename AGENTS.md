# Project Agent Notes

## Terminal Usage
- Prefer running long-lived commands inside `tmux` so work survives SSH/session disconnects.

## Server Information

### Aliyun Production Server
- SSH aliases: `ali`, `myserver`
- Host: `39.108.180.113`
- User: `root`
- Project path: `/root/code/nutrimaster`
- Connect with: `ssh ali` or `ssh myserver`

### Local Development Environment
- Project path: `/data/haotianwu/biojson`

## Useful Transfer Commands
- Copy the server `former-data` directory into the current local directory:
  ```bash
  scp -r ali:/root/code/nutrimaster/former-data .
  ```
- Incrementally sync it into local `./former-data/`:
  ```bash
  rsync -avz ali:/root/code/nutrimaster/former-data/ ./former-data/
  ```
