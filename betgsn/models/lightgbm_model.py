from .base import separated, probabilities


class LightGBMModel:
    version = "LIGHTGBM_V1_EXPERIMENTAL"

    def fit(self, train, validation):
        from lightgbm import LGBMClassifier, early_stopping
        separated(train, validation)
        self.model = LGBMClassifier(n_estimators=300, max_depth=3, num_leaves=7,
            learning_rate=.03, subsample=.8, subsample_freq=1, colsample_bytree=.8,
            reg_lambda=10, reg_alpha=.1, min_child_samples=40, verbosity=-1,
            random_state=6767, n_jobs=2, deterministic=True, force_col_wise=True)
        self.model.fit(train.x, train.y, eval_set=[(validation.x, validation.y)],
                       callbacks=[early_stopping(25, verbose=False)])
        self.trained_until = validation.times[-1]
        return self

    def predict_proba(self, x):
        return probabilities(self.model.predict_proba(x))
