from __future__ import annotations

from dataclasses import dataclass

from app.route_utils import remove_route


@dataclass
class Route:
    path: str
    methods: set[str] | None


class Router:
    def __init__(self, routes: list[Route]) -> None:
        self.routes = routes


class App:
    def __init__(self, routes: list[Route]) -> None:
        self.router = Router(routes)


def test_remove_route_removes_only_matching_path_and_method() -> None:
    get_target = Route("/target", {"GET"})
    post_target = Route("/target", {"POST"})
    get_other = Route("/other", {"GET"})
    app = App([get_target, post_target, get_other])

    remove_route(app, "/target", "get")

    assert app.router.routes == [post_target, get_other]


def test_remove_route_removes_duplicate_registrations() -> None:
    post_target = Route("/target", {"POST"})
    app = App([Route("/target", {"GET"}), Route("/target", {"GET"}), post_target])

    remove_route(app, "/target", "GET")

    assert app.router.routes == [post_target]
