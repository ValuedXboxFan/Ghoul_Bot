from settings import *
import requests
import json
from urllib.parse import quote


class media():

    def __init__(self, i=0, media_type='', title='', year=int()):
        self.media_type = media_type
        self.q_title = title
        self.q_year = year
        self.q_i = i
        if self.media_type == 'movie':
            self.get_movie()
        elif self.media_type == 'tv_show':
            self.get_tv_show()
        elif self.media_type == 'video_game':
            self.get_video_game()
        return


    def get_movie(self):
        self.source = MOVIE_SOURCE
        self.source_legal = MOVIE_SOURCE_LEGAL
        self.source_logo = MOVIE_SOURCE_LOGO
        try:
            if self.q_year:
                response = requests.get(f'https://api.themoviedb.org/3/search/movie?api_key={TMDB_KEY}&language=en-US&query={quote(self.q_title)}&page=1&include_adult=false&year={quote(str(self.q_year))}')
                record = response.json()
            else:
                response = requests.get(f'https://api.themoviedb.org/3/search/movie?api_key={TMDB_KEY}&language=en-US&query={quote(self.q_title)}&page=1&include_adult=false')
                record = response.json()
            self.title = record["results"][self.q_i]["title"]
            self.id = int(record["results"][self.q_i]["id"])
            self.release_date = record["results"][self.q_i]["release_date"]
            self.overview = record["results"][self.q_i]["overview"]
            self.img = f'https://image.tmdb.org/t/p/w300/{record["results"][self.q_i]["poster_path"]}'
        except Exception as e:
            print(f'- Movie retrieval failed: {e}')
        return

    def get_video_game(self):
        self.source = VG_SOURCE
        self.source_legal = VG_SOURCE_LEGAL
        self.source_logo = VG_SOURCE_LOGO

        headers = {'user-key': IGDB_KEY}
        data = (f'search "{self.q_title}"; fields name,id,first_release_date,summary,cover.*; where version_parent = null; limit 5;')

        try:
            if self.q_year:
                response = requests.post('https://api-v3.igdb.com/games/', data=data, headers=headers)
                record = response.json()
            else:
                response = requests.post('https://api-v3.igdb.com/games/', data=data, headers=headers)
                record = response.json()
            self.title = record[self.q_i]["name"]
            self.id = int(record[self.q_i]["id"])
            self.release_date = record[self.q_i]["first_release_date"]
            self.overview = record[self.q_i]["summary"]
            self.img = f'https://images.igdb.com/igdb/image/upload/t_cover_big/{record[self.q_i]["cover"]["image_id"]}.jpg'
            print(self.img)
        except Exception as e:
            print(f'- Video game retrieval failed: {e}')
        return

    def get_tv_show(self):
        self.source = TV_SOURCE
        self.source_legal = TV_SOURCE_LEGAL
        self.source_logo = TV_SOURCE_LOGO
        try:
            if self.q_year:
                response = requests.get(f'https://api.themoviedb.org/3/search/tv?api_key={TMDB_KEY}&language=en-US&page=1&query={quote(self.q_title)}&include_adult=false&first_air_date_year={quote(str(self.q_year))}')
                record = response.json()
            else:
                response = requests.get(f'https://api.themoviedb.org/3/search/tv?api_key={TMDB_KEY}&language=en-US&page=1&query={quote(self.q_title)}&include_adult=false')
                record = response.json()
            self.title = record["results"][self.q_i]["name"]
            self.id = int(record["results"][self.q_i]["id"])
            self.release_date = record["results"][self.q_i]["first_air_date"]
            self.overview = record["results"][self.q_i]["overview"]
            self.img = f'https://image.tmdb.org/t/p/w300/{record["results"][self.q_i]["poster_path"]}'
        except Exception as e:
            print(f'- TV Show retrieval failed: {e}')
        return
