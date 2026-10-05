# Python Lambda base image
FROM public.ecr.aws/lambda/python:3.12

# Install Python dependencies into the Lambda task root
COPY ./src/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -t ${LAMBDA_TASK_ROOT}

# Copy application code. All three functions ship in this one image; each
# Lambda selects its handler through image_config in infra/lambda.tf.
COPY ./src/receiver ${LAMBDA_TASK_ROOT}/receiver
COPY ./src/processor ${LAMBDA_TASK_ROOT}/processor
COPY ./src/notifier ${LAMBDA_TASK_ROOT}/notifier

# Default handler for running the image locally
CMD [ "receiver.lambda_function.lambda_handler" ]
