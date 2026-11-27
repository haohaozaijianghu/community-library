#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

temp_dir=$(mktemp -d)
export KUBECONFIG="$temp_dir/kubeconfig"
cluster="library-check-$$"
app="community-library-app:$(git rev-parse --short=12 HEAD)"
candidate="community-library-app:$(git rev-parse --short=12 HEAD)-candidate"
mock="community-library-mock:$(git rev-parse --short=12 HEAD)"
port_forward=

cleanup() {
    status=$?
    if [[ -n "$port_forward" ]]; then
        kill "$port_forward" 2>/dev/null || true
        wait "$port_forward" 2>/dev/null || true
    fi
    if (( status != 0 )) && [[ -s "$KUBECONFIG" ]]; then
        kubectl get pods,pvc -o wide || true
        kubectl logs -l app=api --tail=50 || true
        kubectl logs -l app=worker -c worker --tail=50 || true
    fi
    kind delete cluster --name "$cluster" || true
    rm -rf "$temp_dir"
}
trap cleanup EXIT

docker build -t "$app" .
docker tag "$app" "$candidate"
docker build -t "$mock" mock
kind create cluster --name "$cluster" --wait 120s
kind load docker-image --name "$cluster" "$app" "$candidate" "$mock"

db_password=$(python3 -c 'import secrets; print(secrets.token_hex(24))')
model_key=$(python3 -c 'import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')
printf 'password=%s\n' "$db_password" > "$temp_dir/db.env"
printf 'DATABASE_URL=postgresql://library:%s@db:5432/library\nLIBRARY_MODEL_SECRET_KEY=%s\n' \
    "$db_password" "$model_key" > "$temp_dir/app.env"
kubectl create secret generic library-db --from-env-file="$temp_dir/db.env"
kubectl create secret generic library-app --from-env-file="$temp_dir/app.env"
rm "$temp_dir/db.env" "$temp_dir/app.env"
kubectl create configmap library-db-schema \
    --from-file=10-library.sql=library/schema.sql --from-file=11-agent.sql=library/agent_schema.sql
sed -e "s|community-library-app:local|$app|g" -e "s|community-library-mock:local|$mock|g" \
    deploy/kind/library.yaml | kubectl apply -f -
for deployment in db mock api worker; do
    kubectl rollout status "deployment/$deployment" --timeout=180s
done

open_api() {
    if [[ -n "$port_forward" ]]; then
        kill "$port_forward" 2>/dev/null || true
        wait "$port_forward" 2>/dev/null || true
    fi
    kubectl port-forward --address 127.0.0.1 service/api :8000 > "$temp_dir/forward" 2>&1 &
    port_forward=$!
    for (( attempt=0; attempt<30; attempt++ )); do
        port=$(sed -nE 's/^Forwarding from 127\.0\.0\.1:([0-9]+) -> 8000$/\1/p' "$temp_dir/forward")
        if [[ -n "$port" ]]; then
            api="http://127.0.0.1:$port"
            if curl -fsS --max-time 2 "$api/healthz" | jq -e '.status == "ok"' >/dev/null; then
                return 0
            fi
        fi
        sleep 1
    done
    cat "$temp_dir/forward" >&2
    return 1
}

auth='Authorization: Bearer demo-north-reader'
submit_and_wait() {
    local task response
    task=$(curl -fsS --max-time 5 "$api/v1/jobs" -H "$auth" -H "Idempotency-Key: $1" \
        -H 'Content-Type: application/json' -d '{"query":"locker"}' | jq -er '.job_id')
    for (( attempt=0; attempt<30; attempt++ )); do
        response=$(curl -fsS --max-time 5 "$api/v1/jobs/$task" -H "$auth")
        case $(jq -r '.status' <<< "$response") in
            succeeded)
                jq -e '.result.citations == ["north-locker-guide"] and .result.usage.cost_units == 200 and (.result.answer | contains("一层服务台旁"))' \
                    <<< "$response" >/dev/null || return 1
                jq -Sc . <<< "$response"
                return 0
                ;;
            failed)
                printf '%s\n' "$response" >&2
                return 1
                ;;
        esac
        sleep 1
    done
    return 1
}

assert_original_task() {
    local restored
    restored=$(curl -fsS --max-time 5 "$api/v1/jobs/$task" -H "$auth" | jq -Sc .)
    [[ "$restored" == "$original" ]]
}

open_api
original=$(submit_and_wait initial)
task=$(jq -er '.job_id' <<< "$original")
echo "Initial rollout and locker smoke test passed ($app)"

for deployment in api worker; do
    kubectl rollout restart "deployment/$deployment"
    kubectl rollout status "deployment/$deployment" --timeout=120s
done
open_api
assert_original_task
submit_and_wait after-restart
echo "API and Worker restarted and the original task survived"

kubectl set image deployment/worker worker="$candidate" wait-for-api="$candidate"
kubectl rollout status deployment/worker --timeout=120s
# The candidate uses the same application image with a deliberately failing release probe.
kubectl patch deployment api --type=strategic -p \
    "{\"spec\":{\"progressDeadlineSeconds\":30,\"template\":{\"spec\":{\"containers\":[{\"name\":\"api\",\"image\":\"$candidate\",\"readinessProbe\":{\"httpGet\":{\"path\":\"/deliberately-unready\",\"port\":8000}}}]}}}}"
if kubectl rollout status deployment/api --timeout=65s > "$temp_dir/rollout" 2>&1; then
    echo "Expected the candidate rollout to fail" >&2
    exit 1
fi
cat "$temp_dir/rollout"
[[ $(kubectl get deployment api -o 'jsonpath={.status.conditions[?(@.type=="Progressing")].reason}') == ProgressDeadlineExceeded ]]

for deployment in api worker; do
    kubectl rollout undo "deployment/$deployment"
    kubectl rollout status "deployment/$deployment" --timeout=120s
    [[ $(kubectl get "deployment/$deployment" -o 'jsonpath={.spec.template.spec.containers[0].image}') == "$app" ]]
done
open_api
assert_original_task
submit_and_wait after-rollback
echo "Failed release was detected, both deployments rolled back, and old and new tasks passed"
