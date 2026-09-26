# Finance network migration

Finance no longer joins the shared application network. Before starting this compose project, create the dedicated external network once:

```sh
docker network create finance_edge
```

Attach only Finance and the Caddy reverse-proxy service to `finance_edge`. Remove Finance from `ggouger_default` after Caddy can reach `finance:8080` over the dedicated network. Do not attach Authelia, databases, or unrelated applications; Caddy reaches Authelia on its existing authentication network.

Verify the boundary after deployment:

```sh
docker network inspect finance_edge
docker inspect finance-finance-1 --format '{{json .NetworkSettings.Networks}}'
```

The network membership must contain exactly the Finance and Caddy containers. Finance must have no published host ports.
