from .base import separated, probabilities


class XGBoostModel:
    version = "XGBOOST_V1_EXPERIMENTAL"

    def fit(self, train, validation):
        from xgboost import XGBClassifier
        separated(train, validation)
        self.model = XGBClassifier(n_estimators=300, max_depth=3, learning_rate=.03,
            subsample=.8, colsample_bytree=.8, reg_lambda=10, reg_alpha=.1,
            min_child_weight=20, early_stopping_rounds=25, random_state=6767,
            n_jobs=2, eval_metric="mlogloss" if len(set(train.y)) > 2 else "logloss")
        self.model.fit(train.x, train.y, eval_set=[(validation.x, validation.y)], verbose=False)
        self.trained_until = validation.times[-1]
        return self

    def predict_proba(self, x):
        return probabilities(self.model.predict_proba(x))
