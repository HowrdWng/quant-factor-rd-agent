from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from quant_factor_agent.domain.schemas import FactorImplementation


class FactorValidationError(ValueError):
    pass


@dataclass(frozen=True)
class FactorCodePolicy:
    function_name: str = "compute_factor"
    max_source_chars: int = 8_000
    allowed_names: frozenset[str] = frozenset(
        {
            "monthly_returns",
            "market_data",
            "returns",
            "close",
            "volume",
            "price_signal",
            "volume_signal",
            "shifted",
            "factor",
            "window",
            "min_periods",
            "np",
            "pd",
            "compute_factor",
            "raw",
            "axis",
            "periods",
            "fill_method",
            "ddof",
        }
    )
    allowed_attributes: frozenset[str] = frozenset(
        {
            "shift",
            "rolling",
            "mean",
            "std",
            "sum",
            "prod",
            "rank",
            "replace",
            "clip",
            "where",
            "abs",
            "pct_change",
            "corr",
            "median",
            "min",
            "max",
            "apply",
            "sqrt",
            "log",
            "sign",
        }
    )

    def validate(self, code: str) -> None:
        if len(code) > self.max_source_chars:
            raise FactorValidationError("generated factor source is too large")
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            raise FactorValidationError(f"factor code is not valid Python: {exc}") from exc

        forbidden_nodes = (
            ast.Import,
            ast.ImportFrom,
            ast.ClassDef,
            ast.AsyncFunctionDef,
            ast.Lambda,
            ast.With,
            ast.AsyncWith,
            ast.Try,
            ast.Raise,
            ast.Global,
            ast.Nonlocal,
            ast.Delete,
            ast.While,
            ast.For,
            ast.AsyncFor,
            ast.ListComp,
            ast.SetComp,
            ast.DictComp,
            ast.GeneratorExp,
        )
        function_defs = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
        if len(tree.body) != 1 or len(function_defs) != 1:
            raise FactorValidationError("source must contain exactly one function definition")
        function = function_defs[0]
        if function.name != self.function_name:
            raise FactorValidationError(f"required function is {self.function_name!r}")
        arguments = [arg.arg for arg in function.args.args]
        if arguments not in (["monthly_returns"], ["market_data"]):
            raise FactorValidationError("compute_factor must accept monthly_returns or market_data")

        has_shift = False
        for node in ast.walk(tree):
            if isinstance(node, forbidden_nodes):
                raise FactorValidationError(f"forbidden syntax: {type(node).__name__}")
            if isinstance(node, ast.Name) and node.id.startswith("__"):
                raise FactorValidationError("dunder names are forbidden")
            if isinstance(node, ast.Attribute):
                if node.attr.startswith("__") or node.attr not in self.allowed_attributes:
                    raise FactorValidationError(f"attribute is not allowed: {node.attr}")
                has_shift = has_shift or node.attr == "shift"
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "shift"
            ):
                periods_node = node.args[0] if node.args else None
                if periods_node is None:
                    periods_node = next(
                        (keyword.value for keyword in node.keywords if keyword.arg == "periods"),
                        None,
                    )
                if (
                    not isinstance(periods_node, ast.Constant)
                    or not isinstance(periods_node.value, int)
                    or periods_node.value < 1
                ):
                    raise FactorValidationError("shift periods must be a constant integer of at least one")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id not in {self.function_name}:
                    raise FactorValidationError(f"direct call is not allowed: {node.func.id}")
        if not has_shift:
            raise FactorValidationError("factor code must explicitly lag inputs with shift")


class FactorRuntime:
    """Development-only AST-restricted factor runtime.

    This rejects common escape routes but is not a security boundary. Production
    execution of untrusted LLM output belongs in the planned Docker runner.
    """

    def __init__(self, policy: FactorCodePolicy | None = None) -> None:
        self.policy = policy or FactorCodePolicy()

    def execute(
        self,
        implementation: FactorImplementation,
        monthly_returns: pd.DataFrame | dict[str, pd.DataFrame],
    ) -> pd.DataFrame:
        self.policy.validate(implementation.code)
        globals_dict: dict[str, Any] = {"__builtins__": {}, "np": np, "pd": pd}
        locals_dict: dict[str, Any] = {}
        compiled = compile(
            implementation.code,
            f"<factor:{implementation.spec.name}>",
            "exec",
        )
        exec(compiled, globals_dict, locals_dict)  # noqa: S102 - guarded development runtime
        function = locals_dict[self.policy.function_name]
        if implementation.spec.input_name == "market_data":
            if not isinstance(monthly_returns, dict):
                raise FactorValidationError("market_data factor requires a field-to-DataFrame mapping")
            input_value = {name: frame.copy() for name, frame in monthly_returns.items()}
            if "returns" not in input_value:
                raise FactorValidationError("market_data must contain returns")
            reference = input_value["returns"]
        else:
            if not isinstance(monthly_returns, pd.DataFrame):
                if "returns" not in monthly_returns:
                    raise FactorValidationError("monthly_returns input is missing")
                reference = monthly_returns["returns"]
            else:
                reference = monthly_returns
            input_value = reference.copy()
        output = function(input_value)
        if not isinstance(output, pd.DataFrame):
            raise FactorValidationError("factor output must be a pandas DataFrame")
        if not output.index.equals(reference.index):
            raise FactorValidationError("factor output index must match input index")
        if list(output.columns) != list(reference.columns):
            raise FactorValidationError("factor output columns must match input columns")
        if np.isinf(output.to_numpy(dtype=float, na_value=np.nan)).any():
            raise FactorValidationError("factor output contains infinity")
        return output.astype(float)
