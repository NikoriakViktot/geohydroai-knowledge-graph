import ray

ray.init()

@ray.remote
def square(x):
    return x * x

futures = [square.remote(i) for i in range(10)]

results = ray.get(futures)

print(results)