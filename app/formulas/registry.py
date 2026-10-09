from app.formulas.base import Formula


class FormulaRegistry:
    def __init__(self):
        self._formulas: dict[str, Formula] = {}

    def register(self, formula: Formula) -> None:
        if not isinstance(formula, Formula):
            raise TypeError("Registered formulas must implement Formula")
        if not getattr(formula, "name", "").strip():
            raise ValueError("Formula name must be nonblank")
        if formula.name in self._formulas:
            raise ValueError(f"Formula already registered: {formula.name}")
        self._formulas[formula.name] = formula

    def get(self, name: str) -> Formula:
        return self._formulas[name]

    def all(self) -> dict[str, Formula]:
        return self._formulas.copy()


# Intentionally empty. Future formula modules register approved implementations.
formula_registry = FormulaRegistry()
