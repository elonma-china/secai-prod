function render(req, res) {
  const name = req.query.name;
  res.send("<h1>Xin chao " + name + "</h1>");
}
module.exports = { render };
