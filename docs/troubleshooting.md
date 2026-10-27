# Troubleshooting and recovery

Start with service state and recent logs:

```sh
docker compose ps
docker compose logs --tail=150 birdframe
docker compose --profile local-audio logs --tail=150 birdnet-go
```

Do not paste tokens, precise coordinates, pairing tokens, or signed package URLs
into issue reports. Use the dashboard's diagnostics export, which redacts
secrets, when available.

| Symptom | Checks |
| --- | --- |
| UI does not load | Confirm `BIRDFRAME_PORT` is free, the container is healthy, and the Linux firewall permits trusted-LAN access. |
| BirdWeather test fails | Verify the station token at BirdWeather, then inspect status/backoff logs without revealing it. 
| BirdFrame cannot reach BirdNET-Go | This Compose configuration uses host networking: set `BIRDNET_GO_URL=http://127.0.0.1:8080`, then inspect upstream logs. |
| EO1 push fails or artwork never appears | Check the EO1 host and port 12345 with `curl http://<eo1>:12345/api/v1/state`, and confirm the EO1 can reach the BirdFrame address over plain `http://`. See [eo1.md](eo1.md). |
| Package install fails | Check catalog URL, download reachability, checksum, package compatibility, and free space. Never bypass checksum errors. |
| Artwork is missing/odd | Review asset approval status, anatomy checks, available storage, OpenRouter job errors, and composition safe-area settings. |

## Safe restart

```sh
docker compose restart birdframe

```
Jobs and cursor state are persisted and should resume safely. A restart does not
delete artwork or EO1 settings.

## Backups and disaster recovery

Stop services and archive `data/` plus `birdnet-go-data/` if used. The BirdFrame
database and `secret.key` are a pair: restoring a database encrypted with a
different key prevents secret decryption. Restore both together, preserve file
ownership, then restart. If the key is irretrievably lost, reset affected
provider credentials through the wizard rather than trying to decrypt them.
