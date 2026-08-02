from halyk_covenants.evaluators.aggregate import CountEvaluator


class ExistenceEvaluator(CountEvaluator):
    metric_type = "existence"
