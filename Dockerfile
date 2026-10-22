# Python Lambda base image
FROM public.ecr.aws/lambda/python:3.12

# Install Python dependencies into the Lambda task root
COPY ./src/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -t ${LAMBDA_TASK_ROOT}

# Copy application code. src/.archive is deliberately left out.
COPY ./src/app.py ${LAMBDA_TASK_ROOT}/
COPY ./src/auth_app.py ${LAMBDA_TASK_ROOT}/
COPY ./src/core ${LAMBDA_TASK_ROOT}/core
COPY ./src/features ${LAMBDA_TASK_ROOT}/features

CMD [ "app.lambda_handler" ]
