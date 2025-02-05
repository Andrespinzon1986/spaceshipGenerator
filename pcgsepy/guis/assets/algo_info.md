This application uses an [evolutionary algorithm](https://en.wikipedia.org/wiki/Evolutionary_algorithm) to generate spaceships. Evolutionary algorithms are a family of optimisation algorithms that use operations such as "mutation" and "crossover" on individuals within a population in order to construct new individuals to satisfy a given goal. In this case, the goal is to generate interesting spaceships that can be successfully piloted in-game.

Each spaceship is defined by a string which describes the spaceship's tiles and rotations. This string is generated by an [L-system](https://wikipedia.org/wiki/L-system) and modified by the [FI-2Pop genetic algorithm](https://www.sciencedirect.com/science/article/abs/pii/S0377221707005668). The fitness of a solution (i.e.: how *good* it is) is based on four different measures we extracted from the most voted spaceships on the Steam Workshop.

The spaceships are subdivided in groups according to their characteristics. The grid you see on the left is the *behavioral grid* of [MAP-Elites](https://arxiv.org/abs/1504.04909). The different configurations you will interact with during the user study rely on different *emitters*, which determine which group of spaceship to use during the automated steps.

If you want to know more about this system, why not check out our previous publications?
Our first paper introduces the L-system and FI-2Pop
> Gallotta, R., Arulkumaran, K., & Soros, L. B. (2022). Evolving Spaceships with a Hybrid L-system Constrained Optimisation Evolutionary Algorithm. In Genetic and Evolutionary Computation Conference Companion. https://dl.acm.org/doi/abs/10.1145/3520304.3528775 

and our second paper explains how we improved the FI-2Pop algorithm to produce valid spaceships more reliably, as well as introducing the MAP-Elites' emitters in our domain
> Gallotta, R., Arulkumaran, K., & Soros, L. B. (2022). Surrogate Infeasible Fitness Acquirement FI-2Pop for Procedural Content Generation. In IEEE Conference on Games. https://ieeexplore.ieee.org/document/9893592

Finally, in our third paper we introduced the preference-learning emitters (PLE) framework, where different types of emitters were used to learn user preferences
> Gallotta, R., Arulkumaran, K., & Soros, L. B. (2022). Preference-Learning Emitters for Mixed-Initiative Quality-Diversity Algorithms. arXiv preprint arXiv:2210.13839. https://arxiv.org/abs/2210.13839)