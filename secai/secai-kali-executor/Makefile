IMAGE ?= secai-kali-executor:latest

.PHONY: build selftest shell size clean

build:
	docker build -t $(IMAGE) .

# Kiểm tra "đủ khả năng": mọi engine in được version.
selftest:
	docker run --rm $(IMAGE) selftest

# Shell debug bên trong image (không hardening).
shell:
	docker run --rm -it --entrypoint /bin/bash $(IMAGE)

size:
	docker image inspect $(IMAGE) --format 'size: {{.Size}} bytes'

clean:
	-docker image rm $(IMAGE)
