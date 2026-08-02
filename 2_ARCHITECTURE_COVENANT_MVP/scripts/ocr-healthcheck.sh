#!/bin/sh
set -eu
python -c "import paddle; assert paddle.is_compiled_with_cuda()"
