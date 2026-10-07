"""Configuración y logging compartidos por los adaptadores."""

from __future__ import annotations

import logging
import os

# Puerto del adaptador REST dentro del contenedor.
PORT = int(os.getenv("PORT", "8000"))

# Origen permitido para el frontend (CORS). En v1 el frontend llama server-side,
# así que el valor por defecto es permisivo solo para desarrollo local.
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")

# Prefijo de los recursos de infraestructura — `variable "project"` de
# `cloud-project-infra/variables.tf`. De aquí salen los nombres de las dos
# tablas DynamoDB con dueño único (principio III): `${PROJECT}-lives` es del
# backend y `${PROJECT}-events-cache` es del microservicio de eventos.
PROJECT = os.getenv("PROJECT", "life-simulator-mvp")

# DynamoDB. El defecto apunta a Floci en local (principio II, "local-first");
# en el clúster se sobreescribe con la variable de entorno del deployment.
DYNAMODB_ENDPOINT = os.getenv("DYNAMODB_ENDPOINT", "http://localhost:4566")
DYNAMODB_REGION = os.getenv("AWS_REGION", "us-east-1")

# Contrato interno gRPC `events.v1` (research, Decisión 4): el microservicio
# es ClusterIP dentro del clúster `backend`, nunca cruza el ALB.
EVENTS_GRPC_ADDRESS = os.getenv("EVENTS_GRPC_ADDRESS", "events.default.svc:50051")
EVENTS_GRPC_TIMEOUT = float(os.getenv("EVENTS_GRPC_TIMEOUT", "1.5"))

_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


def configure_logging() -> None:
    logging.basicConfig(
        level=_LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
