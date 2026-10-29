"""Bedrock billed usage before credit adjustments, from AWS Cost Explorer.

Cost Explorer's service-only total is net of promotional credits. Grouping
by record type preserves the spend even when credits cover the entire bill.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from .base import CollectorError

# This code executes *inside* sudo-secretspec's child. Secret values never enter
# argv, output, or the caller's environment.
_MANAGED_EXEC = (
    "import os,sys; e=os.environ.copy(); "
    "e['AWS_ACCESS_KEY_ID']=e['CODEXBAR_AWS_ACCESS_KEY_ID']; "
    "e['AWS_SECRET_ACCESS_KEY']=e['CODEXBAR_AWS_SECRET_ACCESS_KEY']; "
    "e.pop('AWS_SESSION_TOKEN',None); "
    "os.execvpe(sys.argv[1],sys.argv[1:],e)"
)


class BedrockCostClient:
    """Bounded AWS CLI reads using existing AWS authentication, never installing it."""

    def __init__(self, *, timeout: float = 45, env: Mapping[str, str] | None = None) -> None:
        self.env = dict(os.environ if env is None else env)
        self.deadline = time.monotonic() + timeout
        aws = shutil.which("aws", path=self.env.get("PATH"))
        if aws is None:
            raise CollectorError("Bedrock billing requires the AWS CLI")
        self.prefix = [aws]
        # Let the AWS CLI resolve default profiles, credential_process, and
        # workload roles before consulting this optional machine-local broker.
        self._fallback_prefix: list[str] | None = None
        if self.env.get("CODEXBAR_AWS_ACCESS_KEY_ID") and self.env.get("CODEXBAR_AWS_SECRET_ACCESS_KEY"):
            self._fallback_prefix = [sys.executable, "-c", _MANAGED_EXEC, aws]
        else:
            broker = shutil.which("sudo-secretspec", path=self.env.get("PATH"))
            if broker:
                self._fallback_prefix = [
                    broker,
                    "run",
                    "--reason",
                    "aiuse Bedrock billing",
                    "--",
                    sys.executable,
                    "-c",
                    _MANAGED_EXEC,
                    aws,
                ]
        self._fallback_attempted = False
        self.env["AWS_PAGER"] = ""
        self.env["AWS_CLI_AUTO_PROMPT"] = "off"
        self._account: str | None = None

    def _run(self, args: list[str]) -> dict[str, Any]:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise CollectorError("Bedrock billing query timed out")
        try:
            proc = subprocess.run(
                [*self.prefix, *args, "--output", "json", "--no-cli-pager"],
                env=self.env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=remaining,
                check=False,
                start_new_session=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CollectorError(f"Bedrock AWS CLI failed ({type(exc).__name__})") from exc
        if proc.returncode:
            diagnostic = (proc.stderr or "").casefold()
            missing_credentials = (
                "unable to locate credentials" in diagnostic
                or re.search(r"\((?:nocredentials|nocredentialserror|nocredentialproviders)\)", diagnostic) is not None
            )
            if (
                missing_credentials
                and args[:2] == ["sts", "get-caller-identity"]
                and self._account is None
                and self._fallback_prefix is not None
                and not self._fallback_attempted
            ):
                # Only a definite absence of credentials permits trying another
                # source. Access errors and timeouts must not switch accounts.
                self._fallback_attempted = True
                self.prefix = self._fallback_prefix
                return self._run(args)
            # AWS errors may include request headers or credential diagnostics;
            # expose the category only, never the raw child output.
            category = re.search(r"\((\w+)\)", proc.stderr or "")
            detail = category.group(1) if category else f"exit {proc.returncode}"
            raise CollectorError(f"Bedrock AWS billing unavailable ({detail})")
        try:
            result = json.loads(proc.stdout)
        except ValueError as exc:
            raise CollectorError("Bedrock AWS CLI returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise CollectorError("Bedrock AWS CLI returned a non-object")
        return result

    def account_id(self) -> str:
        if self._account is None:
            result = self._run(["sts", "get-caller-identity", "--region", "us-east-1"])
            account = str(result.get("Account") or "")
            if not re.fullmatch(r"\d{12}", account):
                raise CollectorError("Bedrock AWS account identity unavailable")
            self._account = account
        return self._account

    def fetch(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        moment = (now or datetime.now(UTC)).astimezone(UTC)
        account = self.account_id()
        start = moment.date().replace(day=1).isoformat()
        end = (moment.date() + timedelta(days=1)).isoformat()
        args = [
            "ce",
            "get-cost-and-usage",
            "--region",
            "us-east-1",
            "--time-period",
            f"Start={start},End={end}",
            "--granularity",
            "MONTHLY",
            "--metrics",
            "UnblendedCost",
            "--group-by",
            "Type=DIMENSION,Key=SERVICE",
            "Type=DIMENSION,Key=RECORD_TYPE",
            "--no-paginate",
        ]
        gross = credits = net = 0.0
        estimated = False
        tokens: set[str] = set()
        while True:
            result = self._run(args)
            periods = result.get("ResultsByTime")
            if not isinstance(periods, list) or not periods:
                raise CollectorError("Bedrock billing data unavailable (no billing period)")
            for period in periods:
                if not isinstance(period, dict) or not isinstance(period.get("Groups"), list):
                    raise CollectorError("Bedrock billing data malformed")
                estimated = estimated or bool(period.get("Estimated"))
                for group in period["Groups"]:
                    keys = group.get("Keys") if isinstance(group, dict) else None
                    if not isinstance(keys, list) or len(keys) != 2:
                        raise CollectorError("Bedrock billing group malformed")
                    if "bedrock" not in str(keys[0]).casefold():
                        continue
                    try:
                        metric = group["Metrics"]["UnblendedCost"]
                        amount = float(metric["Amount"])
                        if metric["Unit"] != "USD" or not math.isfinite(amount):
                            raise ValueError
                    except (KeyError, TypeError, ValueError) as exc:
                        raise CollectorError("Bedrock billing amount malformed") from exc
                    net += amount
                    if str(keys[1]).casefold() in {"credit", "refund"}:
                        credits += amount
                    else:
                        gross += amount
            token = result.get("NextPageToken")
            if not token:
                break
            if not isinstance(token, str) or token in tokens or len(tokens) >= 100:
                raise CollectorError("Bedrock billing pagination invalid")
            tokens.add(token)
            args = [
                *(args[: args.index("--next-page-token")] if "--next-page-token" in args else args),
                "--next-page-token",
                token,
            ]
        metadata = {
            "account_id": account,
            "billing_month": start[:7],
            "gross_usd": gross,
            "credits_usd": credits,
            "net_usd": net,
            "estimated": estimated,
        }
        return [
            {
                "provider": "bedrock",
                "source": "aws-cost-explorer",
                "usage": {
                    "loginMethod": f"Spend: ${gross:.2f}",
                    "providerCost": {"used": gross, "currencyCode": "USD", "period": "Monthly"},
                    "updatedAt": moment.isoformat(),
                    "bedrockReadAt": moment.isoformat(),
                    "bedrockBilling": metadata,
                },
            }
        ]


def native_bedrock_payload(
    *, timeout: float = 45, now: datetime | None = None, store: str | None = None, snapshot: str | None = None
) -> Any:
    """One account-scoped, rate-gated billing read, including genuine zero spend."""
    from .bedrock_gate import gated_bedrock_payload

    moment = now or datetime.now(UTC)
    try:
        client = BedrockCostClient(timeout=timeout)
        account = client.account_id()
        return gated_bedrock_payload(
            lambda: client.fetch(now=moment),
            now=moment,
            store=store,
            snapshot=snapshot,
            account_id=account,
            billing_month=moment.astimezone(UTC).strftime("%Y-%m"),
        )
    except CollectorError as exc:
        return exc
    except OSError:
        return CollectorError("Bedrock billing cache unavailable")
