Fix `GET /deploy-spec` returning 500 in the container: the runtime image now ships `deploy/`, which the endpoint reads relative to WORKDIR.
