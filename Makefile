.PHONY: test demo demo-fault demo-whatif demo-budget docker

test:
	python3 -m unittest discover -s tests -v

demo:
	python3 -m dmharness run examples/feature_request.md --out out

demo-fault:
	python3 -m dmharness run examples/feature_request.md --out out_fault --inject-fault

demo-whatif:
	python3 -m dmharness run examples/feature_request.md --out out_whatif --set concurrent_users=300 --set freshness_sla_seconds=900

demo-budget:
	-python3 -m dmharness run examples/feature_request.md --out out_budget --max-steps 2

docker:
	docker build -t dmharness . && docker run --rm -v $$(pwd)/out:/app/out dmharness
