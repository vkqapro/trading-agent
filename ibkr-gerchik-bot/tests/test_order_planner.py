"""Deterministic tests for the canonical pure order planner."""

import ast
import unittest

from src.risk.order_planner import (
    CapitalConstraints,
    ExecutionCosts,
    OrderPlanRequest,
    PlanStatus,
    plan_order,
)


class CanonicalOrderPlannerTests(unittest.TestCase):
    def request(self, **capital):
        return OrderPlanRequest(
            symbol="AAPL", side="BUY", entry=250.0, stop=240.0, target=270.0,
            capital=CapitalConstraints(**capital),
        )

    def test_screener_legacy_amh_reproduces_968(self):
        plan = plan_order(OrderPlanRequest(
            symbol="AMH", side="BUY", entry=30.70920991481053,
            stop=30.207895042594735, target=31.711839659242116,
            capital=CapitalConstraints(account_capital=100_000, max_loss_risk_pct=0.5),
            execution_costs=ExecutionCosts(0.01, 0.005, True),
        ))
        self.assertEqual(plan.quantity, 968)
        self.assertEqual(plan.status, PlanStatus.ACTIONABLE.value)
        self.assertEqual(plan.binding_constraint, "loss_risk")

    def test_manual_legacy_cash_and_risk_parity(self):
        plan = plan_order(OrderPlanRequest(
            symbol="ABC", side="BUY", entry=25.0, stop=20.0, target=35.0,
            capital=CapitalConstraints(account_capital=1_000, max_loss_risk_pct=5,
                                       available_funds=1_000),
            execution_costs=ExecutionCosts(include_in_sizing=False),
        ))
        # Manual: floor(min(1000*5%/5, 1000/25)) = 10.
        self.assertEqual(plan.quantity, 10)
        self.assertEqual(plan.quantity_constraints["loss_risk"], 10)
        self.assertEqual(plan.quantity_constraints["available_funds"], 40)

    def test_dual_constraint_example_allocation_binds(self):
        plan = plan_order(self.request(account_capital=10_000, max_capital_allocation_pct=5,
                                       max_loss_risk_pct=1, available_funds=10_000,
                                       max_position_value=25_000))
        self.assertEqual(plan.quantity, 2)
        self.assertEqual(plan.binding_constraint, "capital_allocation")
        self.assertEqual(plan.quantity_constraints, {"capital_allocation": 2, "loss_risk": 10,
                                                      "available_funds": 40, "max_position_value": 100})
        self.assertEqual(plan.position_value, 500.0)
        self.assertEqual(plan.risk["max_loss_before_costs"], 20.0)
        self.assertEqual(plan.reward["potential_profit_before_costs"], 40.0)
        self.assertEqual(plan.rr, 2.0)

    def test_capital_sensitivity_is_linear_and_deterministic(self):
        observed = []
        for capital in (1_000, 2_000, 5_000, 10_000):
            plan = plan_order(self.request(account_capital=capital,
                                           max_capital_allocation_pct=5,
                                           max_loss_risk_pct=1))
            observed.append((plan.quantity_constraints["capital_allocation"],
                             plan.quantity_constraints["loss_risk"], plan.quantity,
                             plan.binding_constraint, plan.position_value,
                             plan.risk["max_loss_before_costs"],
                             plan.reward["potential_profit_before_costs"], plan.rr))
        self.assertEqual(observed, [
            (0, 1, 0, "capital_allocation", 0.0, 0.0, 0.0, 2.0),
            (0, 2, 0, "capital_allocation", 0.0, 0.0, 0.0, 2.0),
            (1, 5, 1, "capital_allocation", 250.0, 10.0, 20.0, 2.0),
            (2, 10, 2, "capital_allocation", 500.0, 20.0, 40.0, 2.0),
        ])

    def test_each_constraint_can_bind(self):
        cases = [
            ({"account_capital": 10_000, "max_capital_allocation_pct": 1, "max_loss_risk_pct": 10}, "capital_allocation"),
            ({"account_capital": 10_000, "max_capital_allocation_pct": 50, "max_loss_risk_pct": 1}, "loss_risk"),
            ({"account_capital": 10_000, "max_loss_risk_pct": 10, "available_funds": 250}, "available_funds"),
            ({"account_capital": 10_000, "max_loss_risk_pct": 10, "max_position_value": 250}, "max_position_value"),
        ]
        for capital, binding in cases:
            with self.subTest(binding=binding):
                plan = plan_order(self.request(**capital))
                self.assertEqual(plan.binding_constraint, binding)

    def test_zero_quantity_is_non_actionable(self):
        plan = plan_order(self.request(account_capital=10_000, max_capital_allocation_pct=0))
        self.assertEqual(plan.status, "NON_ACTIONABLE")
        self.assertEqual(plan.quantity, 0)
        self.assertEqual(plan.reasons[0]["code"], "ZERO_QUANTITY")

    def test_invalid_long_stop_and_target(self):
        stop_plan = plan_order(OrderPlanRequest("AAPL", "BUY", 100, 100, 110,
                                                CapitalConstraints(10_000, max_loss_risk_pct=1)))
        target_plan = plan_order(OrderPlanRequest("AAPL", "BUY", 100, 90, 100,
                                                  CapitalConstraints(10_000, max_loss_risk_pct=1)))
        self.assertEqual(stop_plan.validation_errors[0]["code"], "INVALID_STOP")
        self.assertEqual(target_plan.validation_errors[0]["code"], "INVALID_TARGET")

    def test_full_precision_is_used(self):
        plan = plan_order(OrderPlanRequest("A", "BUY", 10.009, 9.0, 12.0,
                                          CapitalConstraints(100, max_capital_allocation_pct=10)))
        self.assertEqual(plan.quantity, 0)
        self.assertAlmostEqual(plan.allocation["max_allocated_capital"], 10.0)

    def test_execution_costs_enabled_and_disabled(self):
        enabled = plan_order(OrderPlanRequest("A", "BUY", 10, 9, 12,
            CapitalConstraints(100, max_loss_risk_pct=10), ExecutionCosts(.5, .5, True)))
        disabled = plan_order(OrderPlanRequest("A", "BUY", 10, 9, 12,
            CapitalConstraints(100, max_loss_risk_pct=10), ExecutionCosts(.5, .5, False)))
        self.assertEqual(enabled.quantity, 5)
        self.assertEqual(disabled.quantity, 10)
        self.assertTrue(enabled.risk["execution_costs_included"])
        self.assertFalse(disabled.risk["execution_costs_included"])

    def test_same_input_is_deterministic(self):
        request = self.request(account_capital=10_000, max_capital_allocation_pct=5, max_loss_risk_pct=1)
        self.assertEqual(plan_order(request).to_dict(), plan_order(request).to_dict())

    def test_invalid_values_are_structured(self):
        plan = plan_order(OrderPlanRequest("A", "BUY", float("nan"), 1, 2,
                                          CapitalConstraints(account_capital=-1, max_loss_risk_pct=101,
                                                             available_funds=-1)))
        self.assertEqual(plan.status, "INVALID")
        codes = {error["code"] for error in plan.validation_errors}
        self.assertTrue({"INVALID_PRICE", "INVALID_CAPITAL", "INVALID_PERCENTAGE", "NEGATIVE_LIMIT"} <= codes)

    def test_from_dict_is_strict(self):
        with self.assertRaises(ValueError):
            OrderPlanRequest.from_dict({"symbol": "A", "side": "BUY", "entry": 1, "stop": .5,
                                        "target": 2, "capital": {}, "unexpected": 1})

    def test_planner_module_has_no_io_imports(self):
        tree = ast.parse(open("src/risk/order_planner.py", encoding="utf-8").read())
        imports = {node.names[0].name for node in ast.walk(tree)
                   if isinstance(node, ast.Import) and node.names}
        self.assertFalse(imports & {"socket", "requests", "ib_insync", "pathlib"})


if __name__ == "__main__":
    unittest.main()
