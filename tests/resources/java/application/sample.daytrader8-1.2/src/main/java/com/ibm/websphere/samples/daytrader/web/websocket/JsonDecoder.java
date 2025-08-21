/**
 * (C) Copyright IBM Corporation 2015.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.ibm.websphere.samples.daytrader.web.websocket;

import java.io.StringReader;

import javax.json.Json;
import javax.json.JsonObject;
import javax.websocket.DecodeException;
import javax.websocket.Decoder;
import javax.websocket.EndpointConfig;

public class JsonDecoder implements Decoder.Text<JsonMessage> {

    @Override
    public void destroy() {
    }

    @Override
    public void init(EndpointConfig ec) {
    }

    @Override
    public JsonMessage decode(String json) throws DecodeException {
        JsonObject jsonObject = Json.createReader(new StringReader(json)).readObject();
        
        JsonMessage message = new JsonMessage();
        message.setKey(jsonObject.getString("key"));
        message.setValue(jsonObject.getString("value"));
        
        return message;
    }

    @Override
    public boolean willDecode(String json) {
        try {
            Json.createReader(new StringReader(json)).readObject();
            return true;
          } catch (Exception e) {
            return false;
          }
    }

}
