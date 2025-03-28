def nearest_neighbor(distance_matrix, start=0):
    n = len(distance_matrix)
    unvisited = set(range(n))
    tour = [start]
    unvisited.remove(start)
    current = start

    while unvisited:
        next_city = min(unvisited, key=lambda city: distance_matrix[current][city])
        tour.append(next_city)
        unvisited.remove(next_city)
        current = next_city

    return tour

def two_opt(tour, distance_matrix):
    n = len(tour)
    improved = True
    while improved:
        improved = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                a, b = tour[i - 1], tour[i]
                c, d = tour[j], tour[(j + 1) % n]
                if distance_matrix[a][b] + distance_matrix[c][d] > distance_matrix[a][c] + distance_matrix[b][d]:
                    tour[i:j+1] = reversed(tour[i:j+1])
                    improved = True
        if not improved:
            break
    return tour

def main():
    n = int(input().strip())
    distance_matrix = []
    for _ in range(n):
        row = list(map(int, input().split()))
        distance_matrix.append(row)

    tour = nearest_neighbor(distance_matrix, start=0)
    tour = two_opt(tour, distance_matrix)

    print(n)
    print(" ".join(str(city + 1) for city in tour))

if __name__ == "__main__":
    main()
