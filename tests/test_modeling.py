import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from riskqueue.modeling.train import TrainedModel, calibrate_prefit


def test_prefit_calibration_uses_validation_probabilities():
    training = pd.DataFrame({"signal": [-3.0, -2.0, -1.0, 1.0, 2.0, 3.0]})
    labels = np.array([0, 0, 0, 1, 1, 1])
    estimator = LogisticRegression().fit(training, labels)
    coefficients = estimator.coef_.copy()
    model = TrainedModel("test model", estimator, ["signal"])

    calibrated = calibrate_prefit(model, training, labels)
    probabilities = calibrated.predict_proba(training)

    np.testing.assert_array_equal(estimator.coef_, coefficients)
    assert calibrated.name == "test model + sigmoid calibration"
    assert probabilities.shape == (len(training),)
    assert np.isfinite(probabilities).all()
    assert ((0 <= probabilities) & (probabilities <= 1)).all()
