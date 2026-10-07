"""Repositorio de la vida en DynamoDB (T017).

Tabla `${PROJECT}-lives` con **dueño único: el backend**. Este módulo
NUNCA toca `${PROJECT}-events-cache`, que es propiedad exclusiva del
microservicio de eventos (principio III, sin base de datos compartida).

Esquema de claves (data-model.md §1–§2):

    PK = PLAYER#<uuid v4>
    SK = "STATE"                → la vida en curso (un solo item por jugador)
    SK = EVENT#<seq:06d>        → una fila por período resuelto

El orden lexicográfico de `SK` es el orden cronológico gracias al relleno de
ceros (`06d`): `EVENT#000001` < `EVENT#000010`.

La persistencia sobrevive a recargas y reinicios del backend (FR-008,
SC-006): el estado NO vive en el proceso.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any, Mapping

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from config import DYNAMODB_ENDPOINT, DYNAMODB_REGION, PROJECT
from domain.models import DomainError, Life, LifeEvent

#: Nombre de la tabla dueña del backend. La de eventos es `${PROJECT}-events-cache`.
LIVES_TABLE = f"{PROJECT}-lives"

#: `SK` de la vida en curso. Una única partida por jugador en v1 (FR-008).
STATE_SK = "STATE"


def state_pk(player_id: str) -> str:
    return f"PLAYER#{player_id}"


def event_sk(seq: int) -> str:
    return f"EVENT#{seq:06d}"


def _has_ambient_credentials() -> bool:
    """¿Hay credenciales resolubles por boto3 (env, profile o IRSA)?"""
    return any(
        os.getenv(name)
        for name in (
            "AWS_ACCESS_KEY_ID",
            "AWS_PROFILE",
            "AWS_ROLE_ARN",
            "AWS_WEB_IDENTITY_TOKEN_FILE",
        )
    )


def _to_dynamo(value: Any) -> Any:
    """Convierte tipos de Python en tipos de DynamoDB.

    - `float` → `Decimal` (boto3 rechaza `float` en items).
    - `None` se **omite**: un atributo ausente y un atributo nulo son
      indistinguibles al leer, y `from_dict` ya aplica los defectos.
    """
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, Mapping):
        return {
            key: _to_dynamo(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, (list, tuple)):
        return [_to_dynamo(item) for item in value]
    return value


def _from_dynamo(value: Any) -> Any:
    """Invierte `_to_dynamo`: `Decimal` → `int` o `float`."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {key: _from_dynamo(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(item) for item in value]
    return value


class LivesRepo:
    """Acceso a `${PROJECT}-lives`.

    Los errores de dominio que lanza son exactamente los tres códigos que
    `rest/app.py` traduce a HTTP: `CONFLICT` (409), `NOT_FOUND` (404) e
    `INVALID_ARGUMENT` (422).
    """

    def __init__(
        self,
        table_name: str = LIVES_TABLE,
        endpoint: str | None = DYNAMODB_ENDPOINT,
        region: str = DYNAMODB_REGION,
    ) -> None:
        kwargs: dict[str, Any] = {"region_name": region}
        if endpoint:
            kwargs["endpoint_url"] = endpoint
            if not _has_ambient_credentials():
                # Floci no valida credenciales, pero boto3 sí que firma la
                # petición (principio II: credenciales ficticias, no reales).
                kwargs["aws_access_key_id"] = "test"
                kwargs["aws_secret_access_key"] = "test"
        self._table = boto3.session.Session().resource("dynamodb", **kwargs).Table(
            table_name
        )
        self.table_name = table_name

    # --- vida en curso (SK = STATE) ------------------------------------

    def get_state(self, player_id: str) -> Life | None:
        """`GetItem` de la vida. `None` si todavía no existe (→ 404)."""
        response = self._table.get_item(Key={"PK": state_pk(player_id), "SK": STATE_SK})
        item = response.get("Item")
        if not item:
            return None
        return Life.from_dict(_from_dynamo(item))

    def put_state(self, life: Life, *, create_only: bool = False) -> None:
        """`PutItem` completo de la vida.

        `create_only=True` añade `attribute_not_exists(PK)` para que una
        segunda vida del mismo jugador falle con `409` (una partida en v1).
        """
        life.touch()
        item: dict[str, Any] = {
            "PK": state_pk(life.player_id),
            "SK": STATE_SK,
            **_to_dynamo(life.to_dict()),
        }
        extra: dict[str, Any] = {}
        if create_only:
            extra["ConditionExpression"] = "attribute_not_exists(PK)"
        try:
            self._table.put_item(Item=item, **extra)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == (
                "ConditionalCheckFailedException"
            ):
                raise DomainError(
                    "CONFLICT", "ya existe una vida para este jugador"
                ) from exc
            raise

    def delete_state(self, player_id: str) -> bool:
        """Borra la vida y **todo** su historial (`DELETE /lives`, SC-004).

        Devuelve `True` si había algo que borrar.
        """
        keys = self._all_keys(player_id)
        if not keys:
            return False
        with self._table.batch_writer() as batch:
            for key in keys:
                batch.delete_item(Key=key)
        return True

    # --- historial (SK = EVENT#<seq:06d>) -------------------------------

    def put_event(self, player_id: str, event: LifeEvent) -> None:
        """Escribe la fila del período resuelto (idempotente por `seq`)."""
        item: dict[str, Any] = {
            "PK": state_pk(player_id),
            "SK": event_sk(event.seq),
            **_to_dynamo(event.to_dict()),
        }
        self._table.put_item(Item=item)

    def list_events(self, player_id: str) -> list[LifeEvent]:
        """`Query` ordenado por `SK`: cronológico por construcción."""
        items: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("PK").eq(state_pk(player_id))
            & Key("SK").begins_with("EVENT#"),
            "ScanIndexForward": True,
        }
        while True:
            response = self._table.query(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return [LifeEvent.from_dict(_from_dynamo(item)) for item in items]

    # --- interno --------------------------------------------------------

    def _all_keys(self, player_id: str) -> list[dict[str, str]]:
        keys: list[dict[str, str]] = []
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("PK").eq(state_pk(player_id)),
            "ProjectionExpression": "PK, SK",
        }
        while True:
            response = self._table.query(**kwargs)
            keys.extend(
                {"PK": item["PK"], "SK": item["SK"]} for item in response.get("Items", [])
            )
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return keys
