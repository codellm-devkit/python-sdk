//
// COPYRIGHT LICENSE: This information contains sample code provided in source code form. You may copy, 
// modify, and distribute these sample programs in any form without payment to IBM for the purposes of 
// developing, using, marketing or distributing application programs conforming to the application 
// programming interface for the operating platform for which the sample code is written. 
// Notwithstanding anything to the contrary, IBM PROVIDES THE SAMPLE SOURCE CODE ON AN "AS IS" BASIS 
// AND IBM DISCLAIMS ALL WARRANTIES, EXPRESS OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, ANY IMPLIED 
// WARRANTIES OR CONDITIONS OF MERCHANTABILITY, SATISFACTORY QUALITY, FITNESS FOR A PARTICULAR PURPOSE, 
// TITLE, AND ANY WARRANTY OR CONDITION OF NON-INFRINGEMENT. IBM SHALL NOT BE LIABLE FOR ANY DIRECT, 
// INDIRECT, INCIDENTAL, SPECIAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR OPERATION OF THE 
// SAMPLE SOURCE CODE. IBM HAS NO OBLIGATION TO PROVIDE MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS 
// OR MODIFICATIONS TO THE SAMPLE SOURCE CODE.  
//
// (C) COPYRIGHT International Business Machines Corp., 2001,2011
// All Rights Reserved * Licensed Materials - Property of IBM
//
package com.ibm.websphere.samples.pbw.jpa;

import javax.persistence.Entity;
import javax.persistence.Id;
import javax.persistence.NamedQueries;
import javax.persistence.NamedQuery;
import javax.persistence.Table;
import javax.persistence.Transient;
import javax.persistence.Version;

import com.ibm.websphere.samples.pbw.utils.Util;

/**
 * Inventory is the bean mapping for the INVENTORY table. It provides information about products the
 * store has for sale.
 * 
 * @see Inventory
 */
@Entity(name = "Inventory")
@Table(name = "INVENTORY", schema = "APP")
@NamedQueries({
		@NamedQuery(name = "getItemsByCategory", query = "select i from Inventory i where i.category = :category ORDER BY i.inventoryId"),
		@NamedQuery(name = "getItemsLikeName", query = "select i from Inventory i where i.name like :name"),
		@NamedQuery(name = "removeAllInventory", query = "delete from Inventory") })
public class Inventory implements Cloneable, java.io.Serializable {
	private static final long serialVersionUID = 1L;
	private static final int DEFAULT_MINTHRESHOLD = 50;
	private static final int DEFAULT_MAXTHRESHOLD = 200;
	@Id
	private String inventoryId;
	private String name;
	private String heading;
	private String description;
	private String pkginfo;
	private String image;
	private byte[] imgbytes;
	private float price;
	private float cost;
	private int quantity;
	private int category;
	private String notes;
	private boolean isPublic;
	private int minThreshold;
	private int maxThreshold;
	
	@Version
	private long version;

	@Transient
	private BackOrder backOrder;

	public Inventory() {
	}

	/**
	 * Create a new Inventory.
	 *
	 * @param key
	 *            Inventory Key
	 * @param name
	 *            Name of inventory item.
	 * @param heading
	 *            Description heading of inventory item.
	 * @param desc
	 *            Description of inventory item.
	 * @param pkginfo
	 *            Package info of inventory item.
	 * @param image
	 *            Image of inventory item.
	 * @param price
	 *            Price of inventory item.
	 * @param cost
	 *            Cost of inventory item.
	 * @param quantity
	 *            Quantity of inventory items in stock.
	 * @param category
	 *            Category of inventory item.
	 * @param notes
	 *            Notes of inventory item.
	 * @param isPublic
	 *            Access permission of inventory item.
	 */
	public Inventory(String key, String name, String heading, String desc, String pkginfo, String image, float price,
			float cost, int quantity, int category, String notes, boolean isPublic) {
		this.setInventoryId(key);
		Util.debug("creating new Inventory, inventoryId=" + this.getInventoryId());
		this.setName(name);
		this.setHeading(heading);
		this.setDescription(desc);
		this.setPkginfo(pkginfo);
		this.setImage(image);
		this.setPrice(price);
		this.setCost(cost);
		this.setQuantity(quantity);
		this.setCategory(category);
		this.setNotes(notes);
		this.setIsPublic(isPublic);
		this.setMinThreshold(DEFAULT_MINTHRESHOLD);
		this.setMaxThreshold(DEFAULT_MAXTHRESHOLD);

	}

	/**
	 * Create a new Inventory.
	 *
	 * @param item
	 *            Inventory to use to make a new inventory item.
	 */
	public Inventory(Inventory item) {
		this.setInventoryId(item.getInventoryId());
		this.setName(item.getName());
		this.setHeading(item.getHeading());
		this.setDescription(item.getDescription());
		this.setPkginfo(item.getPkginfo());
		this.setImage(item.getImage());
		this.setPrice(item.getPrice());
		this.setCost(item.getCost());
		this.setQuantity(item.getQuantity());
		this.setCategory(item.getCategory());
		this.setNotes(item.getNotes());
		this.setMinThreshold(DEFAULT_MINTHRESHOLD);
		this.setMaxThreshold(DEFAULT_MAXTHRESHOLD);

		setIsPublic(item.isPublic());

		// does not clone BackOrder info
	}

	/**
	 * Increase the quantity of this inventory item.
	 * 
	 * @param quantity
	 *            The number to increase the inventory by.
	 */
	public void increaseInventory(int quantity) {
		this.setQuantity(this.getQuantity() + quantity);
	}

	public int getCategory() {
		return category;
	}

	public void setCategory(int category) {
		this.category = category;
	}

	public float getCost() {
		return cost;
	}

	public void setCost(float cost) {
		this.cost = cost;
	}

	public String getDescription() {
		return description;
	}

	public void setDescription(String description) {
		this.description = description;
	}

	public String getHeading() {
		return heading;
	}

	public void setHeading(String heading) {
		this.heading = heading;
	}

	public String getImage() {
		return image;
	}

	public void setImage(String image) {
		this.image = image;
	}

	public String getName() {
		return name;
	}

	public void setName(String name) {
		this.name = name;
	}

	public String getNotes() {
		return notes;
	}

	public void setNotes(String notes) {
		this.notes = notes;
	}

	public String getPkginfo() {
		return pkginfo;
	}

	public void setPkginfo(String pkginfo) {
		this.pkginfo = pkginfo;
	}

	public float getPrice() {
		return price;
	}

	public void setPrice(float price) {
		this.price = price;
	}

	public int getQuantity() {
		return quantity;
	}

	public void setQuantity(int quantity) {
		this.quantity = quantity;
	}

	public int getMaxThreshold() {
		return maxThreshold;
	}

	public void setMaxThreshold(int maxThreshold) {
		this.maxThreshold = maxThreshold;
	}

	public int getMinThreshold() {
		return minThreshold;
	}

	public void setMinThreshold(int minThreshold) {
		this.minThreshold = minThreshold;
	}

	public String getInventoryId() {
		return inventoryId;
	}

	public void setInventoryId(String id) {
		inventoryId = id;
	}

	/**
	 * Same as getInventoryId. Added for compatability with ShoppingCartItem when used by the Client
	 * XJB sample
	 * 
	 * @return String ID of the inventory item
	 */
	public String getID() {
		return inventoryId;
	}

	/**
	 * Same as setInventoryId. Added for compatability with ShoppingCartItem when used by the Client
	 * XJB sample
	 * 
	 */
	public void setID(String id) {
		inventoryId = id;
	}

	public boolean isPublic() {
		return isPublic;
	}

	public void setIsPublic(boolean isPublic) {
		this.isPublic = isPublic;
	}

	/** Set the inventory item's public availability. */
	public void setPrivacy(boolean isPublic) {
		setIsPublic(isPublic);
	}

	public byte[] getImgbytes() {
		return imgbytes;
	}

	public void setImgbytes(byte[] imgbytes) {
		this.imgbytes = imgbytes;
	}

	public BackOrder getBackOrder() {
		return backOrder;
	}

	public void setBackOrder(BackOrder backOrder) {
		this.backOrder = backOrder;
	}
	
	@Override
	public String toString() {
	    return getClass().getSimpleName() + "{id=" + inventoryId + "}";
	}

}
