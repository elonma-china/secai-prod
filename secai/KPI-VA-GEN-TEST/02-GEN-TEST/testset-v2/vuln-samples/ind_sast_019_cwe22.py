def read_file(base, filename):
    path = base + "/" + filename
    with open(path) as f:
        return f.read()
