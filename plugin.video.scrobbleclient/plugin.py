"""Plugin entry point: serves the Resume Watching listing."""
import sys

from resources.lib.listing import route

if __name__ == "__main__":
    route(sys.argv)
